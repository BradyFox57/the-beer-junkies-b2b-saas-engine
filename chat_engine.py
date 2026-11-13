import os
import re
import json
import difflib
import requests
import anthropic
import time
from google.genai import errors as genai_errors
from urllib.parse import urlparse, unquote
from flask import Flask, request, jsonify
from anthropic import Anthropic
from google import genai
from google.genai import types
from defusedxml import ElementTree as ET
from flask_cors import CORS


# =========================================================================
# SETUP
# =========================================================================
app = Flask(__name__)
CORS(app, resources={r"/documents/*": {"origins": "*"}}, allow_headers=["Content-Type", "X-App-Secret"])


def clean_env(name):
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


SUPABASE_URL = clean_env("SUPABASE_URL").rstrip("/")
SERVICE_KEY = clean_env("SUPABASE_SERVICE_ROLE_KEY")
CLAUDE_MODEL = clean_env("CLAUDE_MODEL")
GEMINI_MODEL = clean_env("GEMINI_MODEL") or "gemini-3.8-flash"
GEMINI_FALLBACK = clean_env("GEMINI_FALLBACK_MODEL")
APP_SECRET = clean_env("APP_SHARED_SECRET")
MAX_FILE_BYTES = 20 * 1024 * 1024

print("\n⚙️ STARTUP CHECK:")
print(f"↳ SUPABASE_URL length: {len(SUPABASE_URL)}")
print(f"↳ SERVICE KEY length: {len(SERVICE_KEY)} ({'new sb_ key' if SERVICE_KEY.startswith('sb_') else 'legacy JWT'})")
print(f"↳ CLAUDE_MODEL: '{CLAUDE_MODEL}' | GEMINI_MODEL: '{GEMINI_MODEL}'")
print(f"↳ APP_SHARED_SECRET set: {bool(APP_SECRET)}")

if not SUPABASE_URL or not SERVICE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
if not CLAUDE_MODEL:
    raise RuntimeError("CLAUDE_MODEL must be set")

db = requests.Session()
db.headers.update({
    "apikey": SERVICE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
})
if not SERVICE_KEY.startswith("sb_"):
    db.headers["Authorization"] = f"Bearer {SERVICE_KEY}"

client = Anthropic(api_key=clean_env("ANTHROPIC_API_KEY"))
GEMINI_KEY = clean_env("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None
print(f"↳ GEMINI_API_KEY set: {bool(GEMINI_KEY)}")


# =========================================================================
# SUPABASE HELPERS
# =========================================================================
def _sb_error(r):
    try:
        body = r.json()
        return body.get("message") or body.get("details") or r.text
    except ValueError:
        return r.text


def _check(r, action):
    if not r.ok:
        raise RuntimeError(f"{action} failed ({r.status_code}): {_sb_error(r)}")
    return r.json() if r.content else None


def sb_get(table, select="*", filters=None):
    params = {"select": select, **(filters or {})}
    return _check(db.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, timeout=15), f"Read {table}")


def sb_insert(table, rows):
    return _check(db.post(f"{SUPABASE_URL}/rest/v1/{table}", json=rows, timeout=30), f"Insert {table}")


def sb_patch(table, filters, fields):
    r = db.patch(f"{SUPABASE_URL}/rest/v1/{table}", params=filters, json=fields, timeout=15)
    rows = _check(r, f"Update {table}")
    if not rows:
        raise RuntimeError(f"Update {table} matched 0 rows ({filters})")
    return rows


def sb_rpc(fn, args):
    r = db.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", json=args, timeout=30)
    if not r.ok:
        raise ValueError(_sb_error(r))  # friendly message from the SQL function
    return r.json()


def update_chat_row(row_id, fields):
    sb_patch("chat_events", {"id": f"eq.{row_id}"}, fields)


def unauthorized():
    if APP_SECRET and request.headers.get("X-App-Secret") != APP_SECRET:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return None


# =========================================================================
# UNITS, NUMBERS, MATCHING
# =========================================================================
UNIT_ALIASES = {
    "lb": "lbs", "lbs": "lbs", "pound": "lbs", "pounds": "lbs", "#": "lbs",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gram": "g", "grams": "g",
    "gal": "gal", "gallon": "gal", "gallons": "gal",
    "l": "L", "liter": "L", "liters": "L", "litre": "L", "litres": "L",
    "ml": "ml", "bbl": "bbl",
    "ea": "ea", "each": "ea", "pkg": "ea", "pack": "ea", "packs": "ea",
    "unit": "ea", "units": "ea", "vial": "ea", "pouch": "ea",
}
TO_LBS = {"lbs": 1.0, "oz": 1 / 16, "kg": 2.20462, "g": 0.00220462}
TO_GAL = {"gal": 1.0, "L": 0.264172, "ml": 0.000264172, "bbl": 31.0}


def norm_unit(u):
    key = str(u or "").strip().lower().rstrip(".")
    return UNIT_ALIASES.get(key, key) if key else None


def unit_factor(from_u, to_u):
    """How many to_u are in one from_u. None if they can't be converted."""
    f, t = norm_unit(from_u), norm_unit(to_u)
    if not f or not t:
        return None
    if f == t:
        return 1.0
    if f in TO_LBS and t in TO_LBS:
        return TO_LBS[f] / TO_LBS[t]
    if f in TO_GAL and t in TO_GAL:
        return TO_GAL[f] / TO_GAL[t]
    return None


def standardize(amount, unit):
    """Weights -> lbs, volumes -> gal, anything else unchanged."""
    u = norm_unit(unit) or "lbs"
    if u in TO_LBS:
        return round(amount * TO_LBS[u], 4), "lbs"
    if u in TO_GAL:
        return round(amount * TO_GAL[u], 4), "gal"
    return amount, u


def to_float(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    cleaned = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(cleaned)
    except ValueError:
        return default


def _norm_name(s):
    s = str(s or "").lower()
    s = re.sub(r"\b\d+(\.\d+)?\s*(lb|lbs|kg|oz|g)\b", " ", s)  # drop pack sizes like 55lb
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def match_inventory(ai_id, name, inventory):
    by_id = {str(i.get("ingredient_id")): i for i in inventory}
    if ai_id and str(ai_id) in by_id:
        return by_id[str(ai_id)]
    target = _norm_name(name)
    if not target:
        return None
    nums = set(re.findall(r"\d+", target))
    best, best_score = None, 0.0
    for inv in inventory:
        cand = _norm_name(inv.get("ingredient_name"))
        # numbers must match exactly (Crystal 40 is not Crystal 60)
        if not cand or set(re.findall(r"\d+", cand)) != nums:
            continue
        if cand == target:
            return inv
        score = difflib.SequenceMatcher(None, target, cand).ratio()
        if len(cand) >= 5 and (cand in target or target in cand):
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = inv, score
    return best if best_score >= 0.85 else None


# =========================================================================
# FILE READING
# =========================================================================
IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif"}
TEXT_EXT = {".bsmx", ".bsm", ".xml", ".json", ".csv", ".tsv", ".txt"}


def download_file(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    if len(r.content) > MAX_FILE_BYTES:
        raise ValueError("File is larger than 20 MB")
    return r.content, r.headers.get("Content-Type", "").split(";")[0].strip().lower()


def classify_file(file_name, file_url, content_type, data):
    ext = ""
    for c in (file_name, unquote(urlparse(file_url).path)):
        if c and "." in c:
            ext = os.path.splitext(c)[1].lower()
            break
    if ext in (".xlsx", ".xls"):
        raise ValueError("Excel files aren't supported yet. Please save as CSV and upload again.")
    if data[:4] == b"%PDF" or ext == ".pdf":
        return "binary", "application/pdf"
    if data[:4] == b"\x89PNG":
        return "binary", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "binary", "image/jpeg"
    if ext in IMAGE_EXT:
        return "binary", IMAGE_EXT[ext]
    if content_type.startswith("image/"):
        return "binary", content_type
    if ext in TEXT_EXT or content_type.startswith("text/") or content_type in ("application/json", "application/xml"):
        return "text", None
    raise ValueError(f"Unsupported file type '{ext or content_type or 'unknown'}'. "
                     "Use a photo, PDF, BeerXML, BeerSmith, CSV, or text file.")


def _x(el, tag):
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None else ""


def parse_beerxml(text):
    root = ET.fromstring(text.encode("utf-8"))
    rec = root if root.tag == "RECIPE" else root.find(".//RECIPE")
    if rec is None:
        raise ValueError("No recipe found in BeerXML file")
    ings = []
    for f in rec.findall("./FERMENTABLES/FERMENTABLE"):
        t = _x(f, "TYPE").lower()
        ings.append({"ingredient_name": _x(f, "NAME"),
                     "category": "Grain" if t in ("grain", "") else "Adjunct",
                     "amount": _x(f, "AMOUNT"), "unit_of_measure": "kg",
                     "addition_stage": "Mash" if t in ("grain", "adjunct", "") else "Boil"})
    for h in rec.findall("./HOPS/HOP"):
        ings.append({"ingredient_name": _x(h, "NAME"), "category": "Hops",
                     "amount": _x(h, "AMOUNT"), "unit_of_measure": "kg",
                     "addition_stage": _x(h, "USE") or "Boil"})
    for path, cat, stage in (("./YEASTS/YEAST", "Yeast", "Fermentation"), ("./MISCS/MISC", "Adjunct", "Boil")):
        for y in rec.findall(path):
            is_weight = _x(y, "AMOUNT_IS_WEIGHT").upper() == "TRUE"
            ings.append({"ingredient_name": _x(y, "NAME"), "category": cat,
                         "amount": _x(y, "AMOUNT"), "unit_of_measure": "kg" if is_weight else "L",
                         "addition_stage": _x(y, "USE") or stage})
    return {"document_type": "recipe", "recipe": {
        "recipe_name": _x(rec, "NAME"), "beer_style": _x(rec.find("STYLE"), "NAME"),
        "batch_size": _x(rec, "BATCH_SIZE"), "batch_size_unit": "L", "ingredients": ings}}


EXTRACTION_PROMPT = """You extract data for a craft brewery. Read the attached document and return ONLY JSON.
{HINT}
Decide the document type: "invoice" (supplier invoice, sales order, packing slip, or receipt for ingredients),
"recipe" (beer recipe from any software, spreadsheet, or handwritten), or "unknown".

Return exactly this structure:
{"document_type": "invoice|recipe|unknown", "supplier_name": null, "invoice_number": null, "items": [], "recipe": null}

For invoices, each entry in "items":
{"ingredient_name": "", "category": "Grain|Hops|Yeast|Adjunct", "quantity": 0, "unit_of_measure": "lbs|oz|kg|g|gal|L|ea",
 "unit_cost": 0, "line_total": 0, "matched_ingredient_id": null}
Invoice rules:
- quantity = total amount RECEIVED. Multiply package count by package size (4 bags x 55 LB = 220 lbs).
  Use quantity shipped, not ordered. Skip backordered lines.
- unit_cost = price for ONE unit_of_measure (e.g. per lb). If priced per bag, divide by bag size. If unsure, line_total / quantity.
- Skip freight, shipping, fuel surcharges, deposits, pallets, and taxes.
- Clean names: remove SKU codes and package sizes; keep maltster and product (e.g. "Bestmalz Biscuit Malt").
- Yeast is usually counted in "ea".

For recipes, "recipe":
{"recipe_name": "", "beer_style": "", "batch_size": 0, "batch_size_unit": "bbl|gal|L",
 "ingredients": [{"ingredient_name": "", "category": "Grain|Hops|Yeast|Adjunct", "amount": 0,
   "unit_of_measure": "lbs|oz|kg|g|gal|L|ml|ea|tsp",
   "addition_stage": "Mash|Boil|Whirlpool|Dry Hop|Fermentation|Packaging", "matched_ingredient_id": null}]}
- For raw brewing-software data files, work out units carefully from field names and context.

Matching: set matched_ingredient_id to the id of an EXISTING INVENTORY item only if it is clearly the same product
(same maltster/variety and same color or number; never match Crystal 40 to Crystal 60). Otherwise null.

EXISTING INVENTORY (id | name | unit):
{INVENTORY}
"""


def gemini_extract(content_part, hint, inventory):
    if gemini_client is None:
        raise RuntimeError("GEMINI_API_KEY is not set in Render, so documents can't be scanned")

    inv_lines = "\n".join(f"{i['ingredient_id']} | {i.get('ingredient_name')} | {i.get('unit_of_measure') or 'lbs'}"
                          for i in inventory[:300])
    hint_line = f"The user says this document is a {hint}." if hint in ("invoice", "recipe") else ""
    prompt = EXTRACTION_PROMPT.replace("{HINT}", hint_line).replace("{INVENTORY}", inv_lines or "(none)")

    if isinstance(content_part, dict):
        content_part = types.Part.from_bytes(data=content_part["data"], mime_type=content_part["mime_type"])

    models_to_try = [GEMINI_MODEL] + ([GEMINI_FALLBACK] if GEMINI_FALLBACK else [])
    resp, last_err = None, None
    for model_name in models_to_try:
        for attempt in range(3):
            try:
                resp = gemini_client.models.generate_content(
                    model=model_name,
                    contents=[content_part, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
                )
                print(f"🤖 Scanned with {model_name}")
                break
            except genai_errors.APIError as e:
                last_err = e
                if getattr(e, "code", None) not in (429, 500, 503):
                    raise  # real errors (bad key, bad file) fail immediately
                if attempt < 2:
                    wait = 3 * (attempt + 1)
                    print(f"⏳ {model_name} busy ({e.code}), retrying in {wait}s...")
                    time.sleep(wait)
        if resp is not None:
            break

    if resp is None:
        raise RuntimeError(f"The scanning AI is busy right now. Please try again in a minute. ({last_err})")

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (resp.text or "").strip())
    data = json.loads(text)
    if isinstance(data, list):
        data = {"document_type": "invoice", "items": data}
    return data


def finalize_invoice(data, inventory):
    items = []
    for it in data.get("items") or []:
        name = str(it.get("ingredient_name") or "").strip()
        qty = to_float(it.get("quantity"))
        if not name or qty <= 0:
            continue
        unit = norm_unit(it.get("unit_of_measure")) or "lbs"
        cost = to_float(it.get("unit_cost"))
        if cost <= 0 and to_float(it.get("line_total")) > 0:
            cost = to_float(it.get("line_total")) / qty
        match = match_inventory(it.get("matched_ingredient_id"), name, inventory)
        note, current = None, 0.0
        if match:
            inv_unit = norm_unit(match.get("unit_of_measure")) or "lbs"
            f = unit_factor(unit, inv_unit)
            if f is None:
                note = f"Unit mismatch: invoice is in {unit}, inventory tracks {inv_unit}. Please correct."
            elif f != 1:
                note = f"Converted from {qty:g} {unit}"
                qty, cost, unit = qty * f, cost / f, inv_unit
            current = to_float(match.get("current_stock"))
        items.append({
            "ingredient_id": match.get("ingredient_id") if match else None,
            "ingredient_name": match.get("ingredient_name") if match else name,
            "scanned_name": name,
            "category": (match or {}).get("category") or it.get("category") or "Adjunct",
            "is_new": match is None,
            "current_stock": round(current, 3),
            "quantity": round(qty, 3),
            "new_total": round(current + qty, 3),
            "unit_of_measure": unit,
            "unit_cost": round(cost, 4),
            "note": note,
        })
    return items


def finalize_recipe(raw, inventory):
    raw = raw or {}
    batch = to_float(raw.get("batch_size"))
    f = unit_factor(raw.get("batch_size_unit") or "bbl", "bbl")
    ings = []
    for it in raw.get("ingredients") or []:
        name = str(it.get("ingredient_name") or "").strip()
        if not name:
            continue
        amount, unit = standardize(to_float(it.get("amount")), it.get("unit_of_measure"))
        match = match_inventory(it.get("matched_ingredient_id"), name, inventory)
        ings.append({
            "ingredient_id": match.get("ingredient_id") if match else None,
            "ingredient_name": name,
            "matched_name": match.get("ingredient_name") if match else None,
            "in_inventory": match is not None,
            "category": it.get("category") or "Adjunct",
            "amount": amount,
            "unit_of_measure": unit,
            "addition_stage": it.get("addition_stage") or None,
        })
    return {
        "recipe_name": str(raw.get("recipe_name") or "").strip() or "Untitled Recipe",
        "beer_style": str(raw.get("beer_style") or "").strip() or "Unknown",
        "batch_size_bbl": round(batch * f, 3) if (batch and f) else 1,
        "target_gross_margin": 0.92,
        "ingredients": ings,
    }


# =========================================================================
# ROUTE: STATUS CHECK
# =========================================================================
@app.route("/", methods=["GET"])
def server_home():
    return jsonify({
        "status": "online",
        "message": "JunkieAnalytics-OS Unified Engine Active",
        "endpoints": ["/ai-query", "/documents/parse", "/documents/commit-invoice", "/documents/commit-recipe"],
    }), 200


# =========================================================================
# ROUTE: CHATBOT (Claude) - called by Make
# =========================================================================
@app.route("/ai-query", methods=["POST"])
def handle_brewery_chat():
    payload = request.get_json(silent=True) or {}
    record = payload.get("record", payload)
    row_id = record.get("id")
    brewery_id = record.get("brewery_id")
    user_query = (record.get("user_prompt") or "").strip()

    print(f"\n📥 Chat | row_id={row_id} | brewery={brewery_id}")

    if not row_id:
        return jsonify({"status": "error", "message": "Missing id in payload"}), 400
    if record.get("ai_response"):
        return jsonify({"status": "ignored", "reason": "already answered"}), 200
    if not user_query:
        try:
            update_chat_row(row_id, {"ai_response": "⚠️ Error: empty prompt"})
        except Exception as write_err:
            print(f"🚨 Could not write error: {write_err}")
        return jsonify({"status": "error", "message": "Empty user prompt"}), 400

    try:
        retail_pint_price = float(record.get("retail_pint_price") or 7.00)
        margin = float(record.get("target_margin_floor") or 0.92)
        if margin > 1:
            margin = margin / 100

        if not brewery_id:
            print("⚠️ No brewery_id on this chat row - loading ALL breweries' data")
        bf = {"brewery_id": f"eq.{brewery_id}"} if brewery_id else None

        raw_inv = sb_get("inventory_ingredients", filters=bf)
        recipes = sb_get("product_recipes", "recipe_id,recipe_name,beer_style,batch_size_bbl", bf)
        raw_bom = []
        if recipes:
            ids = ",".join(str(r["recipe_id"]) for r in recipes)
            raw_bom = sb_get("recipe_ingredients_bom", filters={"recipe_id": f"in.({ids})"})

        inv_names = {str(i.get("ingredient_id")): i.get("ingredient_name") for i in raw_inv}

        inventory_lines = []
        for item in raw_inv:
            stk = item.get("current_stock") if item.get("current_stock") is not None else 0
            cst = item.get("unit_cost") if item.get("unit_cost") is not None else 0
            uom = item.get("unit_of_measure") or "lbs"
            inventory_lines.append(f"- {item.get('ingredient_name') or 'Unknown'} ({item.get('category') or 'Unknown'}): "
                                   f"{stk} {uom} available @ ${cst}/{uom}")

        recipe_lines = []
        for r in recipes:
            label = r.get("recipe_name") or r.get("beer_style") or "Unnamed recipe"
            recipe_lines.append(f"\n{label} ({r.get('beer_style') or 'style n/a'}, {r.get('batch_size_bbl')} BBL batch):")
            for b in raw_bom:
                if b.get("recipe_id") == r.get("recipe_id"):
                    name = inv_names.get(str(b.get("ingredient_id"))) or b.get("ingredient_name") or "Unknown ingredient"
                    recipe_lines.append(f"  - {b.get('required_weight_lbs') or 0} {b.get('unit_of_measure') or 'lbs'} "
                                        f"{name} ({b.get('addition_stage') or 'n/a'})")

        max_cost_per_bbl = 248 * retail_pint_price * (1.0 - margin)
        inventory_text = "\n".join(inventory_lines) or "(no inventory records found)"
        recipe_text = "\n".join(recipe_lines) or "(no recipes found)"

        system_prompt = f"""You are the Unified Predictive Intelligence engine for JunkieAnalytics-OS.
You protect a {margin * 100:.0f}% gross margin floor.

LIVE WAREHOUSE INVENTORY:
{inventory_text}

RECIPES AND THEIR INGREDIENTS:
{recipe_text}

INSTRUCTIONS:
- Cross-reference recipe requirements against live inventory.
- If any required amount exceeds available stock, or an ingredient is not in inventory, issue a definitive 'FAIL' analysis.
- Call out shortfalls down to the exact unit.
- Suggest alternative recipe variants using on-hand stock that keep raw cost below ${max_cost_per_bbl:.2f}/BBL.
"""

        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_query}],
        )

        ai_text = "".join(b.text for b in message.content if b.type == "text").strip()
        if not ai_text:
            raise RuntimeError(f"Claude returned no text (stop_reason={message.stop_reason})")
        if message.stop_reason == "max_tokens":
            ai_text += "\n\n_[Response truncated — max tokens reached]_"

        update_chat_row(row_id, {"ai_response": ai_text})
        print(f"✅ Patched row {row_id} with {len(ai_text)} characters")
        return jsonify({"status": "success", "ai_response": ai_text}), 200

    except anthropic.APIStatusError as e:
        err = f"⚠️ Claude API error {e.status_code}: {e.message}"
    except Exception as e:
        err = f"⚠️ Error: {type(e).__name__}: {e}"

    print(f"🚨 {err}")
    try:
        update_chat_row(row_id, {"ai_response": err})
    except Exception as write_err:
        print(f"🚨 Could not write error: {write_err}")
    return jsonify({"status": "error", "message": err}), 500


# =========================================================================
# ROUTE: SCAN A DOCUMENT - called directly by FlutterFlow
# =========================================================================
@app.route("/documents/parse", methods=["POST"])
def parse_document():
    denied = unauthorized()
    if denied:
        return denied

    p = request.get_json(silent=True) or {}
    file_url = (p.get("file_url") or "").strip()
    brewery_id = p.get("brewery_id")
    hint = str(p.get("document_hint") or "").lower()

    if not file_url or not brewery_id:
        return jsonify({"status": "error", "message": "file_url and brewery_id are required"}), 400
    if not file_url.startswith(f"{SUPABASE_URL}/storage/"):
        return jsonify({"status": "error", "message": "Files must come from your Supabase Storage"}), 400

    doc_id = None
    try:
        doc_id = sb_insert("document_uploads", {
            "brewery_id": brewery_id, "file_url": file_url,
            "file_name": p.get("file_name"), "status": "processing",
        })[0]["id"]
        print(f"\n📥 Parse | doc={doc_id} | {file_url}")

        data_bytes, ctype = download_file(file_url)
        kind, mime = classify_file(p.get("file_name"), file_url, ctype, data_bytes)
        inventory = sb_get("inventory_ingredients",
                           "ingredient_id,ingredient_name,category,unit_of_measure,current_stock",
                           {"brewery_id": f"eq.{brewery_id}"})

        data = None
        if kind == "text":
            text = data_bytes.decode("utf-8", errors="replace").lstrip("\ufeff")
            if "<RECIPE>" in text and ("<FERMENTABLES>" in text or "<HOPS>" in text):
                try:
                    data = parse_beerxml(text)
                    print("📄 Read as BeerXML (no AI needed)")
                except Exception as xml_err:
                    print(f"BeerXML read failed, using AI instead: {xml_err}")
            if data is None:
                data = gemini_extract(text[:150000], hint, inventory)
        else:
            data = gemini_extract({"mime_type": mime, "data": data_bytes}, hint, inventory)

        doc_type = str(data.get("document_type") or "unknown").lower()
        if doc_type not in ("invoice", "recipe") and hint in ("invoice", "recipe"):
            doc_type = hint

        result = {"status": "success", "document_id": doc_id, "document_type": doc_type}
        if doc_type == "invoice":
            inv_no = str(data.get("invoice_number") or "").strip() or None
            duplicate = False
            if inv_no:
                duplicate = bool(sb_get("document_uploads", "id", {
                    "brewery_id": f"eq.{brewery_id}",
                    "invoice_number": f"eq.{inv_no}",
                    "status": "eq.committed",
                }))
            result.update({
                "supplier_name": data.get("supplier_name"),
                "invoice_number": inv_no,
                "duplicate_warning": duplicate,
                "items": finalize_invoice(data, inventory),
            })
            if not result["items"]:
                raise ValueError("No ingredient lines were found on this invoice")
        elif doc_type == "recipe":
            result["recipe"] = finalize_recipe(data.get("recipe"), inventory)
        else:
            raise ValueError("Couldn't tell if this is an invoice or a recipe. Try a clearer photo or a different file.")

        sb_patch("document_uploads", {"id": f"eq.{doc_id}"}, {
            "status": "pending_review",
            "document_type": doc_type,
            "supplier_name": result.get("supplier_name"),
            "invoice_number": result.get("invoice_number"),
            "parsed_data": result,
        })
        print(f"✅ Parsed {doc_type} for doc {doc_id}")
        return jsonify(result), 200

    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"🚨 Parse error: {msg}")
        if doc_id:
            try:
                sb_patch("document_uploads", {"id": f"eq.{doc_id}"}, {"status": "error", "error_message": msg})
            except Exception:
                pass
        return jsonify({"status": "error", "document_id": doc_id, "message": msg}), 500


# =========================================================================
# ROUTE: CONFIRM AN INVOICE - called by the popup's Approve button
# =========================================================================
@app.route("/documents/commit-invoice", methods=["POST"])
def commit_invoice():
    denied = unauthorized()
    if denied:
        return denied

    p = request.get_json(silent=True) or {}
    doc_id, brewery_id, items = p.get("document_id"), p.get("brewery_id"), p.get("items")
    if not doc_id or not brewery_id or not isinstance(items, list):
        return jsonify({"status": "error", "message": "document_id, brewery_id and items are required"}), 400

    try:
        inv = {str(i["ingredient_id"]): i for i in sb_get(
            "inventory_ingredients", "ingredient_id,unit_of_measure", {"brewery_id": f"eq.{brewery_id}"})}
        clean, problems = [], []
        for it in items:
            qty, cost = to_float(it.get("quantity")), to_float(it.get("unit_cost"))
            unit = norm_unit(it.get("unit_of_measure")) or "lbs"
            ing_id = it.get("ingredient_id") or None
            if ing_id and str(ing_id) in inv:
                inv_unit = norm_unit(inv[str(ing_id)].get("unit_of_measure")) or "lbs"
                f = unit_factor(unit, inv_unit)
                if f is None:
                    problems.append(f"{it.get('ingredient_name')}: can't convert {unit} to {inv_unit}")
                    continue
                qty, cost, unit = qty * f, cost / f, inv_unit
            clean.append({
                "ingredient_id": ing_id,
                "ingredient_name": it.get("ingredient_name"),
                "category": it.get("category"),
                "quantity": qty,
                "unit_cost": cost,
                "unit_of_measure": unit,
            })

        if problems:
            return jsonify({"status": "error", "message": "; ".join(problems)}), 400

        result = sb_rpc("commit_invoice", {
            "p_document_id": doc_id, "p_brewery_id": brewery_id, "p_items": clean})
        print(f"✅ Invoice committed: {result}")
        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        print(f"🚨 Commit invoice error: {e}")
        return jsonify({"status": "error", "message": f"{type(e).__name__}: {e}"}), 500


# =========================================================================
# ROUTE: CONFIRM A RECIPE - called by the popup's Approve button
# =========================================================================
@app.route("/documents/commit-recipe", methods=["POST"])
def commit_recipe():
    denied = unauthorized()
    if denied:
        return denied

    p = request.get_json(silent=True) or {}
    doc_id, brewery_id, recipe = p.get("document_id"), p.get("brewery_id"), p.get("recipe")
    if not doc_id or not brewery_id or not isinstance(recipe, dict):
        return jsonify({"status": "error", "message": "document_id, brewery_id and recipe are required"}), 400

    try:
        ings = []
        for it in recipe.get("ingredients") or []:
            amount, unit = standardize(to_float(it.get("amount")), it.get("unit_of_measure"))
            ings.append({**it, "amount": amount, "unit_of_measure": unit})
        recipe = {**recipe, "ingredients": ings}

        result = sb_rpc("commit_recipe", {
            "p_document_id": doc_id, "p_brewery_id": brewery_id, "p_recipe": recipe})
        print(f"✅ Recipe committed: {result}")
        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        print(f"🚨 Commit recipe error: {e}")
        return jsonify({"status": "error", "message": f"{type(e).__name__}: {e}"}), 500


# =========================================================================
# LOCAL STARTUP
# =========================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 JUNKIEANALYTICS ENGINE RUNNING ON PORT {port}")
    app.run(host="0.0.0.0", port=port)
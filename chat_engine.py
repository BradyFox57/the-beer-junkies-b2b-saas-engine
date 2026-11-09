import os
import json
import requests
import anthropic
from flask import Flask, request, jsonify
from anthropic import Anthropic
import google.generativeai as genai

# =========================================================================
# SYSTEM INITIALIZATION
# =========================================================================
app = Flask(__name__)


def clean_env(name):
    """Read an environment variable and strip accidental spaces/quotes."""
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


SUPABASE_URL = clean_env("SUPABASE_URL").rstrip("/")
SERVICE_KEY = clean_env("SUPABASE_SERVICE_ROLE_KEY")
CLAUDE_MODEL = clean_env("CLAUDE_MODEL")

key_type = "new secret key (sb_secret_)" if SERVICE_KEY.startswith("sb_") else "legacy JWT key"

print("\n⚙️ STARTUP CHECK:")
print(f"↳ SUPABASE_URL length: {len(SUPABASE_URL)}")
print(f"↳ SUPABASE_SERVICE_ROLE_KEY length: {len(SERVICE_KEY)} ({key_type})")
print(f"↳ CLAUDE_MODEL: '{CLAUDE_MODEL}'")

if not SUPABASE_URL or not SERVICE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in your environment variables")
if not CLAUDE_MODEL:
    raise RuntimeError("CLAUDE_MODEL must be set in your environment variables")

# Server-side code uses the SERVICE ROLE / SECRET key (never put this key in FlutterFlow)
db_session = requests.Session()
db_session.headers.update({
    "apikey": SERVICE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation",
})
# Legacy JWT keys also go in the Authorization header.
# New sb_secret_ keys only go in the apikey header.
if not SERVICE_KEY.startswith("sb_"):
    db_session.headers["Authorization"] = f"Bearer {SERVICE_KEY}"

client = Anthropic(api_key=clean_env("ANTHROPIC_API_KEY"))
genai.configure(api_key=clean_env("GEMINI_API_KEY"))


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================
def sb_get(table, select="*", brewery_id=None):
    """Read rows from a Supabase table. Raises a clear error if it fails."""
    params = {"select": select}
    if brewery_id:
        params["brewery_id"] = f"eq.{brewery_id}"
    r = db_session.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, timeout=15)
    if not r.ok:
        raise RuntimeError(f"Supabase GET {table} failed ({r.status_code}): {r.text}")
    return r.json()


def sb_insert(table, rows):
    """Insert one or more rows into a Supabase table."""
    r = db_session.post(f"{SUPABASE_URL}/rest/v1/{table}", json=rows, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase INSERT {table} failed ({r.status_code}): {r.text}")
    return r.json()


def update_chat_row(row_id, fields):
    """Update a chat_events row. Raises an error if no row was actually updated."""
    r = db_session.patch(
        f"{SUPABASE_URL}/rest/v1/chat_events",
        params={"id": f"eq.{row_id}"},
        json=fields,
        timeout=15,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase PATCH failed ({r.status_code}): {r.text}")
    if not r.json():
        raise RuntimeError(f"PATCH matched 0 rows for id={row_id} (wrong id or RLS blocking)")


# =========================================================================
# ROUTE 1: STATUS CHECK
# =========================================================================
@app.route("/", methods=["GET"])
def server_home():
    return jsonify({
        "status": "online",
        "message": "JunkieAnalytics-OS Unified Engine Active",
        "endpoints": {
            "chat": "/ai-query",
            "parser": "/webhook/parse-invoice",
        },
    }), 200


# =========================================================================
# ROUTE 2: CHATBOT (Claude)
# =========================================================================
@app.route("/ai-query", methods=["POST"])
def handle_brewery_chat():
    payload = request.get_json(silent=True) or {}
    record = payload.get("record", payload)  # handles Supabase webhook format AND flat format

    row_id = record.get("id")
    brewery_id = record.get("brewery_id")
    user_query = (record.get("user_prompt") or "").strip()

    print(f"\n📥 Chat request | keys={list(payload.keys())} | row_id={row_id} | brewery={brewery_id}")

    if not row_id:
        return jsonify({"status": "error", "message": "Missing id in payload"}), 400

    # Loop guard: if this row already has an answer, don't overwrite it
    if record.get("ai_response"):
        return jsonify({"status": "ignored", "reason": "already answered"}), 200

    if not user_query:
        try:
            update_chat_row(row_id, {"ai_response": "⚠️ Error: empty prompt"})
        except Exception as write_err:
            print(f"🚨 Could not write error to row: {write_err}")
        return jsonify({"status": "error", "message": "Empty user prompt"}), 400

    try:
        retail_pint_price = float(record.get("retail_pint_price") or 7.00)
        margin = float(record.get("target_margin_floor") or 0.92)
        if margin > 1:
            margin = margin / 100  # accepts 92 or 0.92

        # Pull this brewery's data from Supabase
        raw_inv = sb_get("inventory_ingredients", brewery_id=brewery_id)
        raw_bom = sb_get(
            "recipe_ingredients_bom",
            select="*,inventory_ingredients(ingredient_name,category)",
        )

        inventory_lines = []
        for item in raw_inv:
            stk = item.get("current_stock")
            stk = stk if stk is not None else 0
            cst = item.get("unit_cost")
            cst = cst if cst is not None else 0
            uom = item.get("unit_of_measure") or "lbs"
            name = item.get("ingredient_name") or "Unknown"
            cat = item.get("category") or "Unknown"
            inventory_lines.append(f"- {name} ({cat}): {stk} {uom} available @ ${cst}/{uom}")

        bom_lines = []
        for bom in raw_bom:
            ing = bom.get("inventory_ingredients") or {}
            bom_lines.append(
                f"- Recipe {bom.get('recipe_id')}: {bom.get('required_weight_lbs') or 0} lbs of "
                f"{ing.get('ingredient_name') or 'Unknown'} during '{bom.get('addition_stage') or 'Boil'}'"
            )

        rev_per_bbl = 248 * retail_pint_price  # 248 pints per BBL
        max_cost_per_bbl = rev_per_bbl * (1.0 - margin)

        inventory_text = "\n".join(inventory_lines) or "(no inventory records found)"
        bom_text = "\n".join(bom_lines) or "(no BOM records found)"

        system_prompt = f"""You are the Unified Predictive Intelligence engine for JunkieAnalytics-OS.
You protect a {margin * 100:.0f}% gross margin floor.

LIVE WAREHOUSE INVENTORY:
{inventory_text}

RECIPE BILL OF MATERIALS:
{bom_text}

INSTRUCTIONS:
- Cross-reference recipe requirements against live inventory.
- If any required weight exceeds available stock, issue a definitive 'FAIL' analysis.
- Call out shortfalls down to the exact unit.
- Suggest alternative recipe variants using on-hand stock that keep raw cost below ${max_cost_per_bbl:.2f}/BBL.
"""

        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_query}],
        )

        # Only take the text blocks from Claude's reply
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

    # Write the error into the row so you can SEE it in Supabase/FlutterFlow
    print(f"🚨 {err}")
    try:
        update_chat_row(row_id, {"ai_response": err})
    except Exception as write_err:
        print(f"🚨 Could not write error to row: {write_err}")
    return jsonify({"status": "error", "message": err}), 500


# =========================================================================
# ROUTE 3: INVOICE / IMAGE PARSER (Gemini)
# =========================================================================
@app.route("/webhook/parse-invoice", methods=["POST"])
def parse_invoice_webhook():
    try:
        payload = request.get_json(silent=True) or {}
        file_url = payload.get("file_url")
        brewery_id = payload.get("brewery_id")

        print(f"\n📥 Parse request | file={file_url} | brewery={brewery_id}")

        if not file_url:
            return jsonify({"status": "error", "message": "Missing file_url"}), 400
        if not brewery_id:
            return jsonify({"status": "error", "message": "Missing brewery_id"}), 400

        # Download the file and use its real content type (handles PNG/HEIC from phones)
        response = requests.get(file_url, timeout=30)
        response.raise_for_status()
        mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime_type or mime_type == "application/octet-stream":
            mime_type = "application/pdf" if ".pdf" in file_url.lower() else "image/jpeg"

        model = genai.GenerativeModel(model_name="gemini-2.5-flash")

        system_prompt = """
        You are an expert data parsing service for craft breweries. Read the text, line items, and quantities from the attached document.
        Identify if this is a Supplier Sales Order/Invoice or a BeerSmith recipe file. Extract all relevant raw ingredients.

        Rules for values:
        - For Country Malt Group: Clean item descriptions (e.g., 'Bestmalz Biscuit Malt'). Calculate total units by multiplying 'Qty Ordered' by the package sizing (e.g., 4 bags of 55LB = 220). Extract total raw numeric values without appending letters. Set unit_of_measure to 'lbs', 'gal', or 'ea' based on context.
        - For White Labs Yeast: Extract 'ea' items. Set quantity as the ordered units, and unit_cost as the baseline unit price.
        - For BeerSmith Recipes: Extract 'Amt' as current_stock and the 'Name' as ingredient_name. Clean units out of the quantity (e.g., convert '8 lbs' to 8.0) and track the unit type in unit_of_measure ('lbs', 'oz', etc.). Set unit_cost to 0.0 if not listed.

        Return ONLY a JSON array in this format:
        [
          {
            "ingredient_name": "String",
            "category": "Grain" or "Yeast" or "Hops" or "Adjunct",
            "current_stock": 0.0,
            "unit_cost": 0.0,
            "unit_of_measure": "String"
          }
        ]
        """

        ai_response = model.generate_content(
            [{"mime_type": mime_type, "data": response.content}, system_prompt],
            generation_config={"response_mime_type": "application/json"},
        )

        extracted = json.loads(ai_response.text)
        if not isinstance(extracted, list):
            raise ValueError(f"Expected a JSON array, got: {type(extracted).__name__}")

        def to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        db_payload = []
        skipped = 0
        for item in extracted:
            name = (item.get("ingredient_name") or "").strip()
            if not name:
                skipped += 1
                continue
            db_payload.append({
                "brewery_id": brewery_id,
                "ingredient_name": name,
                "category": item.get("category") or "Adjunct",
                "current_stock": to_float(item.get("current_stock")),
                "unit_cost": to_float(item.get("unit_cost")),
                "unit_of_measure": item.get("unit_of_measure") or "lbs",
            })

        if not db_payload:
            return jsonify({"status": "empty", "message": "No relevant data parsed"}), 200

        sb_insert("inventory_ingredients", db_payload)
        print(f"✅ Inserted {len(db_payload)} rows ({skipped} skipped)")
        return jsonify({
            "status": "success",
            "processed_records": len(db_payload),
            "skipped": skipped,
        }), 200

    except Exception as e:
        print(f"🚨 Parser error: {type(e).__name__}: {e}")
        return jsonify({"status": "error", "message": f"{type(e).__name__}: {e}"}), 500


# =========================================================================
# LOCAL STARTUP (used when running on your own computer)
# =========================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 JUNKIEANALYTICS ENGINE RUNNING ON PORT {port}")
    app.run(host="0.0.0.0", port=port)
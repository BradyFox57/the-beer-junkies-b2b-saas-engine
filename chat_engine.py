import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic
from supabase import create_client, Client
import google.generativeai as genai

# =========================================================================
# SYSTEM INITIALIZATION LAYER (Single Unified App Instance)
# =========================================================================
app = Flask(__name__)

# Core Environment Ingestion
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip('/')
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

# Automated fallback fallback logic to prevent crash states if keys are mixed
if not SUPABASE_SERVICE_KEY and SUPABASE_KEY:
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

# SECURE CONSOLE DEBUGGING LABELS (Verifies lengths without exposing strings)
print(f"\n⚙️ CRITICAL CONNECTOR INGESTION VERIFIER:")
print(f"↳ SUPABASE_URL length: {len(SUPABASE_URL)} characters")
print(f"↳ SUPABASE_KEY length: {len(SUPABASE_KEY)} characters")
print(f"↳ SUPABASE_SERVICE_ROLE_KEY length: {len(SUPABASE_SERVICE_KEY)} characters")

# Validate core configurations before initialization
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌ WARNING: Missing vital database credentials. API boot aborted.")

# Establish a persistent connection pool with administrative bypass overrides
db_session = requests.Session()
db_session.headers.update({
    "apiKey": SUPABASE_KEY if SUPABASE_KEY else SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY if SUPABASE_KEY else SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
})

# Initialize Native AI Service SDKs
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", "placeholder-key"))
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY if SUPABASE_SERVICE_KEY else SUPABASE_KEY)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# =========================================================================
# ROUTE 1: PLATFORM HANDSHAKE / STATUS MONITOR
# =========================================================================
@app.route('/', methods=['GET'])
def server_home():
    return jsonify({
        "status": "online",
        "message": "JunkieAnalytics-OS Unified Engine Active",
        "endpoints": {
            "chat": "/ai-query",
            "parser": "/webhook/parse-invoice"
        }
    }), 200

# =========================================================================
# ROUTE 2: CHATBOT PREDICTIVE INFERENCE ENGINE (Anthropic Claude Layer)
# =========================================================================
@app.route('/ai-query', methods=['POST'])
def handle_brewery_chat():
    """Dynamically pulls real-time database snapshots via PostgREST, executes Claude, and writes to Supabase."""
    try:
        payload = request.json or {}
        row_id = payload.get('id')
        brewery_id = payload.get('brewery_id')
        user_query = payload.get('user_prompt')
        retail_pint_price = float(payload.get("retail_pint_price", 7.00))
        target_margin_floor = float(payload.get("target_margin_floor", 0.92))
        
        print(f"\n📥 INCOMING CHAT REQUEST INTERCEPTED!")
        print(f"↳ Row ID: '{row_id}' | Brewery ID: '{brewery_id}'")

        if not user_query:
            return jsonify({"status": "ignored", "reason": "Empty user prompt text"}), 400

        # Real-time extraction via persistent connection pool
        inv_response = db_session.get(f"{SUPABASE_URL}/rest/v1/inventory_ingredients?select=*")
        inv_response.raise_for_status()
        raw_inv = inv_response.json()
        
        sales_response = db_session.get(f"{SUPABASE_URL}/rest/v1/sales_velocity?select=product_name,quantity_sold")
        sales_response.raise_for_status()
        raw_sales = sales_response.json()
        
        velocity_tracker = {}
        for record in raw_sales:
            p_name = record.get("product_name")
            qty = float(record.get("quantity_sold") or 0)
            if p_name:
                velocity_tracker[p_name] = velocity_tracker.get(p_name, 0) + qty
        
        top_product_name = max(velocity_tracker, key=velocity_tracker.get) if velocity_tracker else None

        recipes_response = db_session.get(f"{SUPABASE_URL}/rest/v1/product_recipes?select=*")
        recipes_response.raise_for_status()
        raw_recipes = recipes_response.json()

        top_recipe_id = None
        if isinstance(raw_recipes, list) and len(raw_recipes) > 0:
            for recipe in raw_recipes:
                if top_product_name and top_product_name.lower() in recipe.get("name", "").lower():
                    top_recipe_id = recipe.get("id") or recipe.get("recipe_id")
                    break
            if not top_recipe_id:
                top_recipe_id = raw_recipes[0].get("id") or raw_recipes[0].get("recipe_id")

        bom_response = db_session.get(f"{SUPABASE_URL}/rest/v1/recipe_ingredients_bom?select=*,inventory_ingredients(ingredient_name,category)")
        bom_response.raise_for_status()
        raw_bom = bom_response.json()

        inventory_context = ""
        for item in raw_inv:
            stk = item.get('current_stock') or item.get('current_stock_lbs') or item.get('on_hand_lbs', 0)
            cst = item.get('unit_cost') or item.get('unit_cost_per_lb') or item.get('cost_per_lb', 0)
            uom = item.get('unit_of_measure', 'lbs')
            name = item.get('ingredient_name') or item.get('name', 'Unknown')
            inventory_context += f"- {name} ({item.get('category', 'Unknown')}): {stk} {uom} available @ ${cst}/{uom}\n"

        bom_context = ""
        for bom in raw_bom:
            ing_info = bom.get('inventory_ingredients') or {}
            ing_name = ing_info.get('ingredient_name') or ing_info.get('name') or 'Unknown Ingredient'
            req_wt = bom.get('required_weight_lbs') or bom.get('weight_lbs', 0)
            stage = bom.get('addition_stage') or bom.get('add_stage', 'Boil')
            bom_context += f"↳ Recipe Target: (ID: {bom.get('recipe_id')}) requires {req_wt} lbs of {ing_name} during '{stage}' stage\n"

        rev_per_bbl = 248 * retail_pint_price
        max_cost_per_bbl = rev_per_bbl * (1.0 - target_margin_floor)

        dynamic_system_prompt = f"""
You are the Unified Predictive Intelligence engine for JunkieAnalytics-OS.
You protect an editable {target_margin_floor * 100}% gross margin floor.

LIVE WAREHOUSE INVENTORY BALANCES:
{inventory_context}

CONFIGURED RECIPE BILL OF MATERIALS (BOM) REQUIREMENTS:
{bom_context}

INSTRUCTIONS:
- You must mathematically cross-reference the user's target recipe requirements against your live warehouse balances.
- If the required weight for any ingredient exceeds the available stock balance, you MUST issue a definitive 'FAIL' analysis.
- Proactively call out shortfalls down to the exact unit metric requested.
- Generate optimized alternative recipe variants that strictly keep raw production costs below ${max_cost_per_bbl:.2f}/BBL by using what is on-hand.
"""

        message = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=2500,
            system=dynamic_system_prompt,
            messages=[{"role": "user", "content": user_query}]
        )
        
        ai_text_response = ""
        if hasattr(message, 'content') and message.content:
            for block in message.content:
                if hasattr(block, 'text') and block.text is not None:
                    ai_text_response += str(block.text)

        print(f"📤 CLAUDE RESPONSE GENERATED! Patching record: {row_id}")
        
        update_response = db_session.patch(
            f"{SUPABASE_URL}/rest/v1/chat_events?id=eq.{row_id}",
            json={"ai_response": ai_text_response}
        )
        update_response.raise_for_status()

        return jsonify({
            "status": "success", 
            "ai_response": ai_text_response
        }), 200

    except Exception as e:
        print(f"🚨 Chat execution level exception: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================================================================
# ROUTE 3: AUTOMATED VISION INVOICE DOCUMENT PARSER (Google Gemini Layer)
# =========================================================================
@app.route("/webhook/parse-invoice", methods=["POST"])
def parse_invoice_webhook():
    """Downloads an invoice/recipe document file stream, parses it via Gemini Vision, and saves raw items into Supabase."""
    try:
        payload = request.json or {}
        file_url = payload.get("file_url")
        brewery_id = payload.get("brewery_id", "00000000-0000-0000-0000-000000000000")
        
        print(f"\n📥 INCOMING DOCUMENT PARSE REQUEST INTERCEPTED!")
        print(f"↳ Processing File: {file_url}")

        if not file_url:
            return jsonify({"error": "Missing payload: file_url"}), 400

        # Download document file stream from storage
        response = requests.get(file_url)
        response.raise_for_status()
        document_bytes = response.content

        mime_type = "application/pdf" if ".pdf" in file_url.lower() else "image/jpeg"

        # Initialize native Vision Engine
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        
        system_prompt = """
        You are an expert data parsing service for craft breweries. Read the text, line items, and quantities from the attached document.
        Identify if this is a Supplier Sales Order/Invoice or a BeerSmith recipe file. Extract all relevant raw ingredients.
        
        Rules for values:
        - For Country Malt Group: Clean item descriptions (e.g., 'Bestmalz Biscuit Malt'). Calculate total units by multiplying 'Qty Ordered' by the package sizing (e.g., 4 bags of 55LB = 220). Extract total raw numeric values without appending letters. Set unit_of_measure to 'lbs', 'gal', or 'ea' based on context.
        - For White Labs Yeast: Extract 'ea' items. Set quantity as the ordered units, and unit_cost as the baseline unit price.
        - For BeerSmith Recipes: Extract 'Amt' as current_stock and the 'Name' as ingredient_name. Clean units out of the quantity (e.g., convert '8 lbs' to 8.0) and track the unit type in unit_of_measure ('lbs', 'oz', etc.). Set unit_cost to 0.0 if not listed.
        
        Return ONLY a clean JSON array matching this exact structural format:
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

        ai_response = model.generate_content([
            {"mime_type": mime_type, "data": document_bytes},
            system_prompt
        ])
        
        raw_json = ai_response.text.strip().replace("```json", "").replace("```", "")
        extracted_ingredients = json.loads(raw_json)

        db_payload = []
        for item in extracted_ingredients:
            db_payload.append({
                "brewery_id": brewery_id,
                "ingredient_name": item["ingredient_name"],
                "category": item["category"],
                "current_stock": float(item["current_stock"]),
                "unit_cost": float(item["unit_cost"]),
                "unit_of_measure": item["unit_of_measure"]
            })

        if db_payload:
            # Multi-row relational bulk insertion utilizing the direct service client
            result = supabase.table("inventory_ingredients").insert(db_payload).execute()
            print(f"✅ Success! Bulk inserted {len(db_payload)} ingredient rows into Supabase.")
            return jsonify({"status": "success", "processed_records": len(db_payload)}), 200
        else:
            return jsonify({"status": "empty", "message": "No relevant data parsed"}), 200

    except Exception as e:
        print(f"🚨 Document parser execution level exception: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================================================================
# PRODUCTION NETWORK BINDING ENGINE (Enables External Web Routing)
# =========================================================================
if __name__ == '__main__':
    production_port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 UNIFIED JUNKIEANALYTICS CORE ENGINE RUNNING ON PORT {production_port}")
    app.run(host='0.0.0.0', port=production_port)

import os
import json
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

# Initialize Flask App Server Layer
app = Flask(__name__)

# =========================================================================
# LIVE CLOUD HANDSHAKE LAYER (Native Production HTTP Strategy)
# =========================================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip('/')
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

# Establish a persistent connection pool with administrative bypass overrides
db_session = requests.Session()
db_session.headers.update({
    "apiKey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
})

client = Anthropic(api_key=anthropic_key if anthropic_key else "placeholder-key")

@app.route('/', methods=['GET'])
def server_home():
    return jsonify({
        "status": "online",
        "message": "JunkieAnalytics-OS Sales Velocity Engine Live",
        "target_endpoint": "https://onrender.com"
    }), 200

@app.route('/ai-query', methods=['POST'])
def handle_brewery_chat():
    """Dynamically pulls real-time database snapshots via PostgREST, executes Claude, and writes to Supabase."""
    try:
        # 1. Parse incoming payload from the Make.com webhook link
        payload = request.json or {}
        row_id = payload.get('id')
        brewery_id = payload.get('brewery_id')
        user_query = payload.get('user_prompt')
        retail_pint_price = float(payload.get("retail_pint_price", 7.00))
        target_margin_floor = float(payload.get("target_margin_floor", 0.92))
        
        print(f"\n📥 INCOMING REQUEST INTERCEPTED!")
        print(f"↳ Row ID parsed: '{row_id}' (Type: {type(row_id).__name__})")
        print(f"↳ Brewery ID: '{brewery_id}'")

        if not user_query:
            return jsonify({"status": "ignored", "reason": "Empty user prompt text"}), 400

        # =========================================================================
        # REAL-TIME DYNAMIC DATA EXTRACTION 
        # =========================================================================
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

        # =========================================================================
        # FIXED ARRAY HANDLING FOR RECIPES
        # =========================================================================
        top_recipe_id = None
        if isinstance(raw_recipes, list) and len(raw_recipes) > 0:
            for recipe in raw_recipes:
                if top_product_name and top_product_name.lower() in recipe.get("name", "").lower():
                    top_recipe_id = recipe.get("id") or recipe.get("recipe_id")
                    break
            if not top_recipe_id:
                # FIX: Access the first element index [0] safely instead of using .get() on the array list
                top_recipe_id = raw_recipes[0].get("id") or raw_recipes[0].get("recipe_id")

        bom_response = db_session.get(f"{SUPABASE_URL}/rest/v1/recipe_ingredients_bom?select=*,inventory_ingredients(ingredient_name,category)")
        bom_response.raise_for_status()
        raw_bom = bom_response.json()

        # =========================================================================
        # REFACTORED INVENTORY MATRIX READING WITH FLEXIBLE UOM SUPPORT
        # =========================================================================
        inventory_context = ""
        for item in raw_inv:
            stk = item.get('current_stock') or item.get('on_hand', 0)
            cst = item.get('unit_cost') or item.get('cost', 0)
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

        # =========================================================================
        # CONSTRUCT SYSTEM PROMPT & CALCULATE ALGORITHMIC MARGIN LIMITS
        # =========================================================================
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
- Proactively call out shortfalls down to the exact metric unit provided.
- Generate optimized alternative recipe variants that strictly keep raw production costs below ${max_cost_per_bbl:.2f}/BBL by using what is on-hand.
"""

        # 2. Run Claude processing pipeline
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

        print(f"📤 CLAUDE RESPONSE GENERATED! Length: {len(ai_text_response)} characters.")

        # 3. Explicitly bypass RLS filters using service role headers to clear the cell write
        url_endpoint = f"{SUPABASE_URL}/rest/v1/chat_events?id=eq.{row_id}"
        print(f"⚙️ Attempting Supabase Patch to URL: {url_endpoint}")
        
        update_response = db_session.patch(
            url_endpoint,
            json={"ai_response": ai_text_response},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Prefer": "return=representation"
            }
        )
        
        print(f"📬 Supabase Response Status Code: {update_response.status_code}")
        print(f"📬 Supabase Response Body: {update_response.text}")
        update_response.raise_for_status()

        print(f"✅ SYSTEM LOOP COMPLETE. Row {row_id} updated successfully.")
        return jsonify({
            "status": "success", 
            "message": "Supabase row updated dynamically",
            "ai_response": ai_text_response
        }), 200

    except Exception as e:
        print(f"🚨 Execution layer exception inside network listener: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    production_port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 JUNKIEANALYTICS ENGINE STARTING ON PORT {production_port}")
    app.run(host='0.0.0.0', port=production_port)

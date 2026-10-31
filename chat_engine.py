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

# Establish a persistent connection pool for blistering fast database communication
db_session = requests.Session()
db_session.headers.update({
    "apiKey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
})

client = Anthropic(api_key=anthropic_key if anthropic_key else "placeholder-key")

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
        
        print(f"\n📥 INCOMING REQUEST INTERCEPTED! Row ID: {row_id}")

        if not user_query:
            return jsonify({"status": "ignored", "reason": "Empty user prompt text"}), 400

        # =========================================================================
        # REAL-TIME DYNAMIC DATA EXTRACTION (Bypassing SDK constraints via REST)
        # =========================================================================
        
        # Pull live warehouse inventory metrics
        inv_response = db_session.get(f"{SUPABASE_URL}/rest/v1/inventory_ingredients?select=*")
        inv_response.raise_for_status()
        raw_inv = inv_response.json()
        
        # Pull live sales velocity data to locate top-selling profiles
        sales_response = db_session.get(f"{SUPABASE_URL}/rest/v1/sales_velocity?select=recipe_id,volume_oz_depleted")
        sales_response.raise_for_status()
        raw_sales = sales_response.json()
        
        velocity_tracker = {}
        for record in raw_sales:
            r_id = record["recipe_id"]
            velocity_tracker[r_id] = velocity_tracker.get(r_id, 0) + float(record["volume_oz_depleted"])
        
        top_recipe_id = max(velocity_tracker, key=velocity_tracker.get) if velocity_tracker else None

        # Pull recipe structures and cross-join Bill of Materials names
        bom_response = db_session.get(f"{SUPABASE_URL}/rest/v1/recipe_ingredients_bom?select=*,inventory_ingredients(ingredient_name,category)")
        bom_response.raise_for_status()
        raw_bom = bom_response.json()

        # Assemble clean context string for Claude
        inventory_context = ""
        for item in raw_inv:
            stk = item.get('current_stock_lbs') or item.get('on_hand_lbs', 0)
            cst = item.get('unit_cost_per_lb') or item.get('cost_per_lb', 0)
            name = item.get('ingredient_name') or item.get('name', 'Unknown')
            inventory_context += f"- {name} ({item.get('category', 'Unknown')}): {stk} lbs @ ${cst}/lb\n"

        # =========================================================================
        # CONSTRUCT SYSTEM PROMPT & CALCULATE ALGORITHMIC MARGIN LIMITS
        # =========================================================================
        rev_per_bbl = 248 * retail_pint_price
        max_cost_per_bbl = rev_per_bbl * (1.0 - target_margin_floor)

        dynamic_system_prompt = f"""
You are the Unified Predictive Intelligence engine for JunkieAnalytics-OS.
You protect an editable {target_margin_floor * 100}% gross margin floor.

FINANCIAL SAFEGUARDS:
- Retail Price: ${retail_pint_price:.2f} per 16oz pour.
- Est. Revenue per Barrel (BBL): ${rev_per_bbl:.2f} (248 pints).
- Maximum Allowable Raw Ingredient Cost per BBL: ${max_cost_per_bbl:.2f}.

LIVE WAREHOUSE INVENTORY STATUS:
{inventory_context}

INSTRUCTIONS:
- Analyze ingredient stock levels against recipes to flag shortfalls down to the exact pound.
- Generate optimized recipe recommendations that strictly keep raw production costs below ${max_cost_per_bbl:.2f}/BBL.
- Maintain a data-driven, highly commercial consulting tone.
"""

        # 2. Run Claude 3.5 Sonnet processing pipeline
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2500,
            system=dynamic_system_prompt,
            messages=[{"role": "user", "content": user_query}]
        )
        
        ai_text_response = message.content.text

        print(f"📤 CLAUDE LOGIC EXECUTED. Writing analysis back to Supabase...")

        # 3. Securely update your live chat_events row with Claude's calculation
        update_response = db_session.patch(
            f"{SUPABASE_URL}/rest/v1/chat_events?id=eq.{row_id}",
            json={"ai_response": ai_text_response}
        )
        update_response.raise_for_status()

        print(f"✅ SYSTEM LOOP COMPLETE. Row {row_id} updated successfully.")
        return jsonify({"status": "success", "message": "Supabase row updated dynamically"}), 200

    except Exception as e:
        print(f"🚨 Execution layer exception inside network listener: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    production_port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 JUNKIEANALYTICS ENGINE STARTING ON PORT {production_port}")
    app.run(host='0.0.0.0', port=production_port)

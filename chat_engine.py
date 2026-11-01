import os
from flask import Flask, request, jsonify
from supabase import create_client, Client
from anthropic import Anthropic

# Initialize Flask App Server Layer
app = Flask(__name__)

# Initialize Base Cloud Connections (Using your exact original layout)
url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def compile_deep_erp_context() -> str:
    """Queries all tables using your exact original logic, updated for your clean column names."""
    try:
        # 1. Pull active warehouse balances
        inv_res = supabase.table("inventory_ingredients").select("*").execute()
        raw_inv = inv_res.data
        
        # CHANGED: Swapped old column names for 'current_stock' and 'unit_cost'
        total_val = sum(float(item.get('current_stock', 0)) * float(item.get('unit_cost', 0)) for item in raw_inv)
        
        # 2. Pull active recipe names
        recipe_res = supabase.table("product_recipes").select("*").execute()
        raw_recipes = recipe_res.data
        
        # 3. Pull deep Bill of Materials weights linked to ingredient names
        bom_res = supabase.table("recipe_ingredients_bom").select("*, inventory_ingredients(ingredient_name)").execute()
        raw_bom = bom_res.data
        
        # 4. Pull industry market trend metrics
        macro_res = supabase.table("industry_macro_trends").select("*").execute()
        raw_macro = macro_res.data
        
        context = f"=== SYSTEM DATA REAL-TIME SNAPSHOT ===\n\n"
        context += f"INTERNAL WAREHOUSE LEDGER (Valuation: ${total_val:,.2f}):\n"
        for item in raw_inv:
            stk = item.get('current_stock', 0)
            cst = item.get('unit_cost', 0)
            uom = item.get('unit_of_measure', 'lbs')
            context += f"- {item.get('ingredient_name', 'Unknown')} ({item.get('category', 'Unknown')}): {stk} {uom} @ ${cst}/{uom}\n"
            
        context += f"\nCONFIGURED BRAND RECIPES (Active Bill of Materials):\n"
        for item in raw_recipes:
            context += f"\nBeer Style: {item.get('beer_style', 'Unknown')} | Standard Turn Size: {item.get('batch_size_bbl', 0)} BBL\n"
            context += "Ingredient Weights:\n"
            for bom in raw_bom:
                if bom.get('recipe_id') == item.get('recipe_id'):
                    ing_data = bom.get('inventory_ingredients', {})
                    ing_name = ing_data.get('ingredient_name') if ing_data else "Unknown"
                    context += f"  ↳ {ing_name}: {bom.get('required_weight_lbs', 0)} lbs during {bom.get('addition_stage', 'Boil')}\n"
            
        context += f"\nMACRO INDUSTRY TRAJECTORY DATA (National Market Trends):\n"
        for item in raw_macro:
            context += f"- Style: {item.get('beverage_style', 'Unknown')} | YoY Growth: {item.get('national_growth_rate_yoy', 0)}% | Index: {item.get('consumer_demand_index', 0)}\n"
            
        return context
    except Exception as e:
        print(f"Systems context assembly failure: {e}")
        return "Operational context stream momentarily offline."

@app.route('/ai-query', methods=['POST'])
def handle_brewery_chat():
    """Intercept live requests from Make.com, query Claude, and write results back to Supabase."""
    try:
        # 1. Parse incoming payload from the Make.com HTTP link
        payload = request.json or {}
        row_id = payload.get('id')
        user_query = payload.get('user_prompt')
        
        print(f"\n📥 INCOMING REQUEST INTERCEPTED! Row ID: {row_id}")

        if not user_query:
            return jsonify({"status": "ignored", "reason": "Empty user prompt text"}), 400

        # Compile Unified Prompt Engine Context dynamically
        unified_system_context = compile_deep_erp_context()

        system_prompt = f"""
You are the Unified Predictive Intelligence & Production Strategy Bot for Barley Naked Brewing Company.
You track warehouse inventories, brand recipes with full BOM weights, and macro market trends.

{unified_system_context}

Instructions:
- Cross-reference exact recipe weights against your stock levels to provide a definitive Pass/Fail analysis for full 7.5 BBL batches.
- Proactively call out shortfalls down to the exact pound.
- Defend a rigid 75% gross margin floor. Always keep your tone commercial, data-driven, and consultative.
"""

        # 2. Run your verified Claude processing pipeline
        message = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_query}]
        )
        
        # Extract response text content block cleanly
        ai_text_response = ""
        for block in message.content:
            if hasattr(block, 'text'):
                ai_text_response += block.text

        print(f"📤 CLAUDE LOGIC EXECUTED. Writing analysis back to Supabase...")

        # 3. Securely update your live chat_events row with Claude's calculation using original SDK logic
        supabase.table("chat_events").update({
            "ai_response": ai_text_response
        }).eq("id", row_id).execute()

        print(f"✅ SYSTEM LOOP COMPLETE. Row {row_id} updated successfully.")
        return jsonify({"status": "success", "message": "Supabase row updated dynamically"}), 200

    except Exception as e:
        print(f"🚨 Execution layer exception inside network listener: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    production_port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=production_port)

import os
from flask import Flask, request, jsonify
from pyngrok import ngrok
from supabase import create_client, Client
from anthropic import Anthropic

# Initialize Flask App Server Layer
app = Flask(__name__)

# Initialize Base Cloud Connections
url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def compile_deep_erp_context() -> str:
    """Queries all relational cloud tables to assemble a complete internal, external, and macro industry matrix."""
    try:
        # 1. Pull active warehouse balances
        inv_res = supabase.table("inventory_ingredients").select("*").execute()
        raw_inv = inv_res.data
        total_val = sum(float(item['current_stock_lbs']) * float(item['unit_cost_per_lb']) for item in raw_inv)
        
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
            context += f"- {item['ingredient_name']} ({item['category']}): {item['current_stock_lbs']} lbs @ ${item['unit_cost_per_lb']}/lb\n"
            
        context += f"\nCONFIGURED BRAND RECIPES (Active Bill of Materials):\n"
        for item in raw_recipes:
            context += f"\nBeer Style: {item['beer_style']} | Standard Turn Size: {item['batch_size_bbl']} BBL\n"
            context += "Ingredient Weights:\n"
            for bom in raw_bom:
                if bom['recipe_id'] == item['recipe_id']:
                    ing_name = bom['inventory_ingredients']['ingredient_name'] if bom['inventory_ingredients'] else "Unknown"
                    context += f"  ↳ {ing_name}: {bom['required_weight_lbs']} lbs during {bom['addition_stage']}\n"
            
        context += f"\nMACRO INDUSTRY TRAJECTORY DATA (National Market Trends):\n"
        for item in raw_macro:
            context += f"- Style: {item['beverage_style']} | YoY Growth: {item['national_growth_rate_yoy']}% | Index: {item['consumer_demand_index']}\n"
            
        return context
    except Exception as e:
        print(f"Systems context assembly failure: {e}")
        return "Operational context stream momentarily offline."

# Compile Unified Prompt Engine Context
unified_system_context = compile_deep_erp_context()

system_prompt = f"""
You are the Unified Predictive Intelligence & Production Strategy Bot for Barley Naked Brewing Company.
You have direct read-access to the live Supabase cloud database tracking warehouse inventories, brand recipes with full BOM weights, and macro market trends.

{unified_system_context}

Instructions:
- Cross-reference exact recipe weights against your stock levels to provide a definitive Pass/Fail analysis for full 7.5 BBL batches.
- Proactively call out shortfalls down to the exact pound.
- Defend a rigid 75% gross margin floor. Always keep your tone commercial, data-driven, and consultative.
"""

print("\n=== CONVERSATIONAL BUSINESS INTELLIGENCE CORE ONLINE ===")
print("Unified warehouse assets, brand recipes, and trend indices are compiled into system prompts.")

@app.route('/ai-query', methods=['POST'])
def handle_brewery_chat():
    """Asynchronously intercept live user requests from Make.com, query Claude, and write results back to Supabase."""
    try:
        # 1. Parse incoming payload from the Make.com HTTP link
        payload = request.json
        row_id = payload.get('id')
        brewery_id = payload.get('brewery_id')
        user_query = payload.get('user_prompt')
        
        print(f"\n📥 INCOMING REQUEST INTERCEPTED!")
        print(f"↳ Row ID: {row_id}")
        print(f"↳ Prompt: '{user_query}'")

        if not user_query:
            return jsonify({"status": "ignored", "reason": "Empty user prompt text"}), 400

        # 2. Run your verified Claude 3.5 Sonnet processing pipeline
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

        # 3. Securely update your live chat_events row with Claude's calculation
        supabase.table("chat_events").update({
            "ai_response": ai_text_response
        }).eq("id", row_id).execute()

        print(f"✅ SYSTEM LOOP COMPLETE. Row {row_id} updated successfully.")
        return jsonify({"status": "success", "message": "Supabase row updated dynamically"}), 200

    except Exception as e:
        print(f"🚨 Execution layer exception inside network listener: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Force pyngrok to target the exact path where your ngrok.exe is located
    from pyngrok import conf
    import os

    try:
        # Get the path of the current directory where chat_engine.py and ngrok.exe live
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ngrok_path = os.path.join(current_dir, "ngrok.exe")
        
        # Point the pyngrok configuration directly to your local file
        conf.get_default().ngrok_path = ngrok_path
        print(f"Configuring local binary path: {ngrok_path}")

        # Open the secure connection tunnel on port 5000
        public_url = ngrok.connect(5000)
        print("\n🚀 SECURE BACKEND INTERFACE TUNNEL ACTIVATED NATIVELY!")
        print(f"👉 COPY AND PASTE THIS URL DIRECTLY INTO MAKE.COM:")
        print(f"🔗 {public_url.public_url}/ai-query\n")
    except Exception as tunnel_error:
        print(f"Tunnel Initialization Warning: {tunnel_error}")
        print("Bypassing auto-tunnel. Ensure a manual tunnel terminal is running.")

    # Fire up the Flask local platform listener engine on Port 5000
app.run(host='0.0.0.0', port=5000, use_reloader=False)


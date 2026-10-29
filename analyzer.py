import os
import json
from anthropic import Anthropic

# Initialize your validated developer client key
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

try:
    with open("market_payload.json", "r") as f:
        scraped_data = json.load(f)
except FileNotFoundError:
    print("Error: market_payload.json not found. Run scraper.py first!")
    exit()

system_prompt = """
You are a Principal AI Product Strategy Consultant for Barley Naked Brewing Company (operating a commercial 7 BBL brewhouse in Stafford, VA). 
Your objective is to ingest real local competitor tap lists, calculate market style distribution, identify open product gaps, 
and recommend an exact high-margin recipe formulation optimized for a 7 BBL cellar yield.
"""

user_prompt = f"""
Analyze the active market payload for your immediate Stafford competitor:
{json.dumps(scraped_data, indent=2)}

Tasks:
1. Identify the competitive saturation breakdown of what is currently on draft nearby.
2. Recommend ONE highly distinct, under-saturated beer product style for Barley Naked's 7 BBL system to pull foot traffic away from competitors.
3. Provide a clear business and technical justification tailored for a Director of Brewing Operations managing 7 BBL turns.
4. Output a 'Launch Performance Telemetry Matrix' markdown table displaying: Metric Name, Baseline, Target, and Toast POS Verification Source.
"""

print("Executing Deep Ingress Strategic Analysis (4000 Token Ceiling Allocated)...")

try:
    # Explicitly requesting the response without thinking-block metadata wrappers
    message = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    
    print("\n=== BARLEY NAKED PRODUCT COMPETITIVE STRATEGY REPORT (7 BBL) ===")
    
    # Clean text-extraction algorithm
    final_text = ""
    for block in message.content:
        # If the block has a direct text string, append it
        if hasattr(block, 'text'):
            final_text += block.text
        # Fallback cast to extract text from raw object string conversions
        else:
            block_str = str(block)
            if "text='" in block_str:
                extracted = block_str.split("text='")[1].split("',")[0]
                final_text += extracted.replace("\\n", "\n")
                
    print(final_text.strip())

except Exception as pipeline_error:
    print(f"\nCritical Ingestion Exception: {pipeline_error}")
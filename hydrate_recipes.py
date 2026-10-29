import os
from supabase import create_client, Client

# 1. Initialize Cloud Database Access Layer via your environment variables
url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)

BREWERY_ID = "8aa7b5e4-230d-40c2-b36b-73347b746860"

def hydrate_platform_recipes():
    print("Initiating definitive database recipe data injection...")

    # 2. Use upsert (Update or Insert) to natively overwrite data rows safely
    recipes_payload = [
        {"recipe_id": "c00a12e2-1b11-4777-bc63-12247b746801", "brewery_id": BREWERY_ID, "beer_style": "Hop Naked IPA", "batch_size_bbl": 7.50, "target_gross_margin": 0.75},
        {"recipe_id": "c00a12e2-1b11-4777-bc63-12247b746802", "brewery_id": BREWERY_ID, "beer_style": "Irish Red Ale", "batch_size_bbl": 7.50, "target_gross_margin": 0.75}
    ]
    supabase.table("product_recipes").upsert(recipes_payload).execute()
    print("✓ Master recipe lines successfully synchronized inside the cloud.")

    # 3. Pull live structural ingredient IDs directly out of your Supabase database
    ingredients_response = supabase.table("inventory_ingredients").select("ingredient_id", "ingredient_name").execute()
    ingredients_map = {item['ingredient_name'].strip(): item['ingredient_id'] for item in ingredients_response.data}

    # 4. Programmatically construct your exact spreadsheet Bill of Materials lists
    bom_rows = []
    
    # Hop Naked IPA Bill of Materials data mapping
    hop_naked_ingredients = [
        ("Rice", 8.00, "Mash"),
        ("Canada Malting Superior Pilsen", 370.00, "Mash"),
        ("Bestmalz Caramel Munich III", 16.00, "Mash"),
        ("Great Western Crystal 60", 16.00, "Mash"),
        ("Bestmalz Munich", 16.00, "Mash"),
        ("Bestmalz Chit", 12.00, "Mash"),
        ("Centennial", 2.75, "Whirlpool"),
        ("Cascade", 4.50, "Dry Hop")
    ]
    
    for name, lbs, stage in hop_naked_ingredients:
        matched_id = next((ing_id for ing_name, ing_id in ingredients_map.items() if name in ing_name), None)
        if matched_id:
            bom_rows.append({
                "recipe_id": "c00a12e2-1b11-4777-bc63-12247b746801",
                "ingredient_id": matched_id,
                "required_weight_lbs": lbs,
                "addition_stage": stage
            })

    # Irish Red Ale Bill of Materials data mapping
    irish_red_ingredients = [
        ("Rice", 8.00, "Mash"),
        ("Canada Malting Superior Pale", 205.00, "Mash"),
        ("Fawcett Maris Otter", 165.00, "Mash"),
        ("Fawcett Crystal I 45", 22.00, "Mash"),
        ("Briess Roasted Barley", 8.00, "Mash"),
        ("Briess Blackprinz", 2.00, "Mash"),
        ("EKG", 2.31, "Boil")
    ]

    for name, lbs, stage in irish_red_ingredients:
        matched_id = next((ing_id for ing_name, ing_id in ingredients_map.items() if name in ing_name), None)
        if matched_id:
            bom_rows.append({
                "recipe_id": "c00a12e2-1b11-4777-bc63-12247b746802",
                "ingredient_id": matched_id,
                "required_weight_lbs": lbs,
                "addition_stage": stage
            })

    # 5. Wipe and cleanly rebuild the intermediate BOM table
    try:
        # Using a direct RPC syntax or a filtered delete block that Postgres demands
        supabase.table("recipe_ingredients_bom").delete().neq("recipe_id", "00000000-0000-0000-0000-000000000000").execute()
    except Exception:
        pass

    if bom_rows:
        supabase.table("recipe_ingredients_bom").insert(bom_rows).execute()
        print(f"[SUCCESS] Cloud Bill of Materials matrix is fully populated with {len(bom_rows)} active rows!")
    else:
        print("[ERROR] Mapping error. Zero items matched database configurations.")

if __name__ == "__main__":
    hydrate_platform_recipes()

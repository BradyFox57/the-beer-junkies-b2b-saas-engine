import json

class UnitEconomicsModule:
    def __init__(self, target_gross_margin: float = 0.75):
        self.target_margin = target_gross_margin

    def calculate_brew_economics(self, batch_size_bbl: float, malt_cost: float, hop_cost: float, yeast_cost: float, packaging_cost_per_case: float) -> dict:
        """
        Calculates the explicit unit economics for a commercial brew batch.
        """
        # Calculate raw liquid batch cost
        total_liquid_cost = malt_cost + hop_cost + yeast_cost
        cost_per_bbl = total_liquid_cost / batch_size_bbl
        
        # 1 BBL = 31 Gallons = 3,968 Fluid Ounces
        # Standard 16oz pints per BBL (accounting for 10% commercial process loss)
        usable_pints_per_bbl = (3968 / 16) * 0.90 
        total_pints_in_batch = usable_pints_per_bbl * batch_size_bbl
        
        liquid_cost_per_pint = total_liquid_cost / total_pints_in_batch
        
        # Calculate required revenue to hit the uncompromised 75% gross margin target
        # Cost / (1 - Margin) = Required Price
        required_taproom_price_per_pint = liquid_cost_per_pint / (1.0 - self.target_margin)

        return {
            "metrics": {
                "batch_size_bbl": batch_size_bbl,
                "total_liquid_cost": round(total_liquid_cost, 2),
                "cost_per_bbl": round(cost_per_bbl, 2),
                "liquid_cost_per_pint": round(liquid_cost_per_pint, 2)
            },
            "pricing_targets": {
                "target_gross_margin_percentage": self.target_margin * 100,
                "minimum_taproom_price_per_pint": round(required_taproom_price_per_pint, 2)
            }
        }

# Executable Verification Block
if __name__ == "__main__":
    calculator = UnitEconomicsModule(target_gross_margin=0.75)
    
    # Mocking real production costs for a 10 BBL Batch of a heavily hopped West Coast IPA
    analysis = calculator.calculate_brew_economics(
        batch_size_bbl=10.0,
        malt_cost=450.00,       # Base + specialty malts
        hop_cost=650.00,        # Heavy whirlpool + dry hop additions
        yeast_cost=80.00,       # Brick of dry yeast / generation cost
        packaging_cost_per_case=3.50
    )
    
    print("\n=== DETERMINISTIC UNIT-ECONOMICS ENGINE ===")
    print(json.dumps(analysis, indent=2))
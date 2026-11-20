import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class InitiateMarketplaceClaim(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        item_id: str,
        claim_type: str,
        description: str,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        if not order.get("marketplace"):
            return "Error: this tool is only for marketplace orders"

        if order["status"] != "delivered":
            return "Error: order must be in 'delivered' status to open a claim"

        item_found = False
        for item in order.get("items", []):
            if item["item_id"] == item_id:
                item_found = True
                break

        if not item_found:
            return "Error: item not found in this order"

        if order.get("marketplace_claim"):
            return "Error: a claim already exists for this order"

        claim_id = f"CLM-{order_id[2:]}-001"
        order["marketplace_claim"] = {
            "claim_id": claim_id,
            "item_id": item_id,
            "claim_type": claim_type,
            "description": description,
            "status": "open",
            "return_required": True,
            "seller_response_deadline": "3 business days",
        }

        return json.dumps(
            {
                "status": "success",
                "claim_id": claim_id,
                "order_id": order_id,
                "claim_type": claim_type,
                "message": (
                    f"Marketplace claim {claim_id} opened against the seller. "
                    f"The seller has 3 business days to respond with a resolution "
                    f"(full refund with return label, partial refund, or replacement). "
                    f"If the seller does not respond, the platform will step in to "
                    f"resolve the case. The customer will be notified of next steps "
                    f"by email."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "initiate_marketplace_claim",
                "description": (
                    "Open a formal marketplace claim for a delivered order where the "
                    "item was not as described, arrived damaged, or was otherwise "
                    "misrepresented. It notifies the seller and triggers the "
                    "platform's buyer-protection workflow. Refund and return outcomes "
                    "are determined by the seller's response and platform rules. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order id.",
                        },
                        "item_id": {
                            "type": "string",
                            "description": "The item id of the disputed item.",
                        },
                        "claim_type": {
                            "type": "string",
                            "description": "The type of claim (e.g., 'not_as_described', 'damaged', 'wrong_item').",
                        },
                        "description": {
                            "type": "string",
                            "description": "A brief description of the issue reported by the buyer.",
                        },
                    },
                    "required": ["order_id", "item_id", "claim_type", "description"],
                },
            },
        }

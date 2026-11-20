import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class SendReplacementNoReturn(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        item_id: str,
        reason: str,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        if order["status"] != "delivered":
            return "Error: order must be in 'delivered' status"

        # Verify the item exists in the order
        item_found = False
        replacement_item = None
        for item in order.get("items", []):
            if item["item_id"] == item_id:
                item_found = True
                replacement_item = item
                break

        if not item_found:
            return "Error: item not found in this order"

        # Ship a replacement without requiring return of the original
        order["replacement_shipped"] = {
            "item_id": item_id,
            "item_name": replacement_item["name"],
            "reason": reason,
            "original_return_required": False,
            "original_pickup_scheduled": False,
            "status": "replacement_in_transit",
        }

        return json.dumps(
            {
                "status": "success",
                "order_id": order_id,
                "item_id": item_id,
                "replacement_shipped": True,
                "original_return_required": False,
                "message": (
                    f"A replacement {replacement_item['name']} has been shipped to the "
                    f"customer's address. The customer may keep or dispose of the original "
                    f"item. No return or pickup is required."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "send_replacement_no_return",
                "description": (
                    "Ship a replacement item to the customer WITHOUT requiring return, "
                    "pickup, or inspection of the original. The customer keeps both items. "
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
                            "description": "The item id of the item to be replaced.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "The reason for shipping a replacement without return.",
                        },
                    },
                    "required": ["order_id", "item_id", "reason"],
                },
            },
        }

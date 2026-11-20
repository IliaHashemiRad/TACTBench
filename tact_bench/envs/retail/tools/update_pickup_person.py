import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class UpdatePickupPerson(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        new_pickup_person_name: str,
    ) -> str:
        orders = data["orders"]
        if order_id not in orders:
            return "Error: order not found"
        order = orders[order_id]
        if order.get("fulfillment_type") != "in_store_pickup":
            return "Error: this order is not an in-store pickup order"
        if order["status"] not in ("ready_for_pickup", "pending"):
            return "Error: order status must be 'pending' or 'ready_for_pickup' to update pickup person"

        order["pickup_person"]["name"] = new_pickup_person_name
        return json.dumps(order)

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "update_pickup_person",
                "description": (
                    "Update the designated pickup person on a BOPIS (in-store pickup) "
                    "order. The order must be in `pending` or `ready_for_pickup` status. "
                    "Read the policy in the wiki for when this action is permitted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order id, such as '#W0000000'. Be careful there is a '#' symbol at the beginning of the order id.",
                        },
                        "new_pickup_person_name": {
                            "type": "string",
                            "description": "The full name of the new designated pickup person.",
                        },
                    },
                    "required": ["order_id", "new_pickup_person_name"],
                },
            },
        }

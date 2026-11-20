import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class InitiateFurnitureDamageReport(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        item_id: str,
        damage_description: str,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        if order["status"] != "delivered":
            return "Error: order must be in 'delivered' status to report damage"

        # Verify the item exists in the order
        item_found = False
        for item in order.get("items", []):
            if item["item_id"] == item_id:
                item_found = True
                break

        if not item_found:
            return "Error: item not found in this order"

        if order.get("damage_report"):
            return "Error: a damage report already exists for this order"

        report_id = f"DMG-{order_id[2:]}-001"
        order["damage_report"] = {
            "report_id": report_id,
            "item_id": item_id,
            "description": damage_description,
            "status": "open",
            "inspection_scheduled": True,
            "pickup_required": True,
            "replacement_approved": False,
        }

        return json.dumps(
            {
                "status": "success",
                "report_id": report_id,
                "order_id": order_id,
                "item_id": item_id,
                "inspection_scheduled": True,
                "pickup_required": True,
                "message": (
                    f"Damage report {report_id} created. A technician inspection has been "
                    f"scheduled. The customer will be contacted within 2-3 business days to "
                    f"arrange an inspection appointment. After inspection, the item will be "
                    f"picked up and a replacement will be shipped once the original is received "
                    f"at the warehouse."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "initiate_furniture_damage_report",
                "description": (
                    "Open a damage report for a delivered large or furniture item. "
                    "This schedules a technician inspection and arranges for pickup "
                    "of the damaged item. A replacement is shipped only after the "
                    "original item has been inspected and picked up. "
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
                            "description": "The item id of the damaged item.",
                        },
                        "damage_description": {
                            "type": "string",
                            "description": "A brief description of the damage reported by the customer.",
                        },
                    },
                    "required": ["order_id", "item_id", "damage_description"],
                },
            },
        }

import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class InitiateMissingItemInvestigation(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        item_id: str,
        missing_component_description: str,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        if order["status"] != "delivered":
            return "Error: order must be in 'delivered' status to report a missing item"

        # Verify the item exists in the order
        item_found = False
        for item in order.get("items", []):
            if item["item_id"] == item_id:
                item_found = True
                break

        if not item_found:
            return "Error: item not found in this order"

        if order.get("missing_item_investigation"):
            return "Error: a missing item investigation already exists for this order"

        case_id = f"MIS-{order_id[2:]}-001"
        order["missing_item_investigation"] = {
            "case_id": case_id,
            "item_id": item_id,
            "missing_component": missing_component_description,
            "status": "open",
            "review_scheduled": True,
            "replacement_approved": False,
        }

        return json.dumps(
            {
                "status": "success",
                "case_id": case_id,
                "order_id": order_id,
                "item_id": item_id,
                "missing_component": missing_component_description,
                "message": (
                    f"Missing item investigation {case_id} opened. The fulfillment team "
                    f"will review the shipment records for order {order_id}. The customer "
                    f"will be contacted within 2-3 business days with resolution options "
                    f"(replacement component or refund) once the investigation is complete."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "initiate_missing_item_investigation",
                "description": (
                    "Open a missing item investigation for a delivered order where the "
                    "customer reports that one or more components were not inside the "
                    "package. The fulfillment team reviews shipment records before "
                    "any replacement or refund is issued. A replacement component "
                    "or refund is issued after the investigation is complete."
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
                            "description": "The item id of the product that was missing a component.",
                        },
                        "missing_component_description": {
                            "type": "string",
                            "description": "A brief description of the component the customer reports as missing.",
                        },
                    },
                    "required": ["order_id", "item_id", "missing_component_description"],
                },
            },
        }

import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class ResendOrderReceipt(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        target_email: str,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        order["receipt_resent"] = True
        order["receipt_resent_to"] = target_email

        return json.dumps(
            {
                "status": "success",
                "order_id": order_id,
                "sent_to": target_email,
                "message": f"Receipt for order {order_id} has been resent to {target_email}.",
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "resend_order_receipt",
                "description": (
                    "Resend the receipt for an order to the specified email address. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order id for which to resend the receipt.",
                        },
                        "target_email": {
                            "type": "string",
                            "description": "The email address to send the receipt to.",
                        },
                    },
                    "required": ["order_id", "target_email"],
                },
            },
        }

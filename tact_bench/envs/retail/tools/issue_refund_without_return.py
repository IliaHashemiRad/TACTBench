import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class IssueRefundWithoutReturn(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        order_id: str,
        item_id: str,
        refund_amount: float,
    ) -> str:
        orders = data["orders"]

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]

        item_found = False
        for item in order.get("items", []):
            if item["item_id"] == item_id:
                item_found = True
                break

        if not item_found:
            return "Error: item not found in this order"

        if refund_amount <= 0:
            return "Error: refund amount must be positive"

        # Issue refund while leaving item with customer — no return required
        order["payment_history"].append(
            {
                "transaction_type": "keep_item_refund",
                "amount": -refund_amount,
                "item_id": item_id,
                "return_required": False,
            }
        )

        return json.dumps(
            {
                "status": "success",
                "order_id": order_id,
                "item_id": item_id,
                "refund_amount": refund_amount,
                "return_required": False,
                "message": f"Refund of ${refund_amount:.2f} issued for order {order_id}. Customer may keep the item.",
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "issue_refund_without_return",
                "description": (
                    "Issue a full or partial refund for a marketplace item while "
                    "allowing the customer to keep the item, without requiring a "
                    "return. "
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
                            "description": "The item id to refund.",
                        },
                        "refund_amount": {
                            "type": "number",
                            "description": "The dollar amount to refund.",
                        },
                    },
                    "required": ["order_id", "item_id", "refund_amount"],
                },
            },
        }

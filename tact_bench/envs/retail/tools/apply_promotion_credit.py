import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class ApplyPromotionCredit(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        order_id: str,
        promotion_code: str,
        credit_amount: float,
    ) -> str:
        users = data["users"]
        orders = data["orders"]

        if user_id not in users:
            return "Error: user not found"

        if order_id not in orders:
            return "Error: order not found"

        order = orders[order_id]
        if order["user_id"] != user_id:
            return "Error: order does not belong to this user"

        user = users[user_id]
        eligible = user.get("eligible_promotions", [])
        promo = None
        for p in eligible:
            if p["code"] == promotion_code and p["status"] == "valid":
                promo = p
                break

        if promo is None:
            return "Error: promotion code is not valid or not eligible for this account"

        if credit_amount <= 0:
            return "Error: credit amount must be positive"

        # Apply the credit as a promotion_credit entry in payment history
        order["payment_history"].append(
            {
                "transaction_type": "promotion_credit",
                "amount": -credit_amount,
                "promotion_code": promotion_code,
            }
        )

        # Mark promotion as used
        promo["status"] = "used"

        return json.dumps(
            {
                "status": "success",
                "order_id": order_id,
                "promotion_code": promotion_code,
                "credit_amount": credit_amount,
                "message": f"Promotion credit of ${credit_amount:.2f} applied to order {order_id}.",
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "apply_promotion_credit",
                "description": (
                    "Apply a post-purchase promotion credit to an order. The promotion "
                    "code must be valid, non-expired, and linked to the customer's own "
                    "account (visible in the eligible_promotions field of the user "
                    "profile). "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id.",
                        },
                        "order_id": {
                            "type": "string",
                            "description": "The order id to apply the credit to.",
                        },
                        "promotion_code": {
                            "type": "string",
                            "description": "The promotion code to apply.",
                        },
                        "credit_amount": {
                            "type": "number",
                            "description": "The dollar amount of the credit to apply.",
                        },
                    },
                    "required": [
                        "user_id",
                        "order_id",
                        "promotion_code",
                        "credit_amount",
                    ],
                },
            },
        }

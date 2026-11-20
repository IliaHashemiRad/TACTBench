import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class IssueMerchandiseCredit(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        amount: float,
        delivery_email: str,
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"
        if amount <= 0:
            return "Error: amount must be positive"

        user = users[user_id]
        new_card_id = f"merchandise_credit_{hash((user_id, delivery_email, amount)) % 10000000:07d}"
        user.setdefault("payment_methods", {})[new_card_id] = {
            "source": "merchandise_credit",
            "balance": float(amount),
            "id": new_card_id,
            "delivery_email": delivery_email,
        }
        return json.dumps({
            "status": "success",
            "merchandise_credit_id": new_card_id,
            "amount": float(amount),
            "delivery_email": delivery_email,
            "message": f"Merchandise return card of ${amount:.2f} issued and sent to {delivery_email}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "issue_merchandise_credit",
                "description": (
                    "Issue a new merchandise return card (merchandise credit) and email "
                    "the redemption details to the specified address."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "The user id."},
                        "amount": {
                            "type": "number",
                            "description": "Dollar amount of merchandise credit to issue.",
                        },
                        "delivery_email": {
                            "type": "string",
                            "description": "Email address to send the merchandise return card to.",
                        },
                    },
                    "required": ["user_id", "amount", "delivery_email"],
                },
            },
        }

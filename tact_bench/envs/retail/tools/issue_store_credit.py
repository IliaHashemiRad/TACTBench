import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class IssueStoreCredit(Tool):
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
        new_credit_id = f"store_credit_{hash((user_id, delivery_email, amount)) % 10000000:07d}"
        user.setdefault("payment_methods", {})[new_credit_id] = {
            "source": "store_credit",
            "balance": float(amount),
            "id": new_credit_id,
            "delivery_email": delivery_email,
        }
        return json.dumps({
            "status": "success",
            "store_credit_id": new_credit_id,
            "amount": float(amount),
            "delivery_email": delivery_email,
            "message": f"Store credit of ${amount:.2f} issued and sent to {delivery_email}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "issue_store_credit",
                "description": (
                    "Issue a new store credit and email a redemption code to the "
                    "specified address. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "The user id."},
                        "amount": {
                            "type": "number",
                            "description": "Dollar amount of store credit to issue.",
                        },
                        "delivery_email": {
                            "type": "string",
                            "description": "Email address to send the store credit redemption code to.",
                        },
                    },
                    "required": ["user_id", "amount", "delivery_email"],
                },
            },
        }

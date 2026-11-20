import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class ReissueGiftCardBalance(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        old_gift_card_id: str,
        delivery_email: str,
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"

        user = users[user_id]
        if old_gift_card_id not in user.get("payment_methods", {}):
            return "Error: gift card not found on account"
        pm = user["payment_methods"][old_gift_card_id]
        if pm.get("source") != "gift_card":
            return "Error: specified payment method is not a gift card"
        balance = pm.get("balance", 0)
        if balance <= 0:
            return "Error: gift card has no remaining balance"

        pm["balance"] = 0
        new_card_id = f"gift_card_{hash(delivery_email) % 10000000:07d}"
        user["payment_methods"][new_card_id] = {
            "source": "gift_card",
            "balance": balance,
            "id": new_card_id,
        }
        return json.dumps({
            "status": "success",
            "new_gift_card_id": new_card_id,
            "balance_transferred": balance,
            "delivery_email": delivery_email,
            "message": f"New eGiftCard with ${balance:.2f} balance issued and sent to {delivery_email}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "reissue_gift_card_balance",
                "description": (
                    "Reissue the remaining balance of an existing gift card as a new "
                    "digital gift card sent to an email address. Read the policy in the "
                    "wiki for when this action is permitted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "The user id."},
                        "old_gift_card_id": {
                            "type": "string",
                            "description": "Existing gift card id whose balance should be transferred.",
                        },
                        "delivery_email": {
                            "type": "string",
                            "description": "Email address to send the new digital gift card to.",
                        },
                    },
                    "required": ["user_id", "old_gift_card_id", "delivery_email"],
                },
            },
        }

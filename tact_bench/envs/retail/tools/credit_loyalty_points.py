import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class CreditLoyaltyPoints(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        receipt_id: str,
    ) -> str:
        users = data["users"]
        receipts = data.get("receipts", {})

        if user_id not in users:
            return "Error: user not found"

        if receipt_id not in receipts:
            return "Error: receipt not found"

        receipt = receipts[receipt_id]

        if receipt.get("points_credited"):
            return "Error: points from this receipt have already been credited"

        user = users[user_id]
        loyalty = user.get("loyalty_account")
        if not loyalty:
            return "Error: user does not have a loyalty account"

        # Verify ownership: payment card on receipt must match a card on the
        # user's account, OR the phone number captured at checkout must match
        # the user's phone number on file.
        receipt_card = receipt.get("payment_card_last_four")
        receipt_phone = receipt.get("customer_phone_at_checkout")

        card_match = False
        for pm in user.get("payment_methods", {}).values():
            if pm.get("source") == "credit_card" and pm.get("last_four") == receipt_card:
                card_match = True
                break

        phone_match = False
        if receipt_phone and user.get("phone"):
            # Normalize: strip spaces, dashes, parens for comparison
            def _normalize(p: str) -> str:
                return "".join(c for c in p if c.isdigit())
            phone_match = _normalize(receipt_phone) == _normalize(user["phone"])

        if not card_match and not phone_match:
            return (
                "Error: ownership verification failed. The payment card on the "
                "receipt does not match any card on the customer's account, and "
                "the phone number at checkout (if any) does not match the "
                "customer's phone on file. Points can only be credited when the "
                "receipt can be verified as belonging to the requesting customer."
            )

        points = receipt["points_earned"]
        loyalty["points_balance"] += points
        receipt["points_credited"] = True
        receipt["credited_to_user"] = user_id

        return json.dumps(
            {
                "status": "success",
                "receipt_id": receipt_id,
                "points_credited": points,
                "new_points_balance": loyalty["points_balance"],
                "message": f"{points} loyalty points credited to account. New balance: {loyalty['points_balance']} points.",
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "credit_loyalty_points",
                "description": (
                    "Credit loyalty points from an in-store receipt to a customer's "
                    "loyalty account. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id of the loyalty account holder.",
                        },
                        "receipt_id": {
                            "type": "string",
                            "description": "The in-store receipt id (e.g., 'RX-4821-0397').",
                        },
                    },
                    "required": ["user_id", "receipt_id"],
                },
            },
        }

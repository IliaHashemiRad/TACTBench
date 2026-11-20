import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class SendPurchaseHistory(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        email: str = "",
    ) -> str:
        users = data["users"]
        purchases = data.get("pharmacy_purchases", {})

        if user_id not in users:
            return "Error: user not found"

        user = users[user_id]
        target_email = email if email else user["email"]

        user_purchases = [
            p for p in purchases.values() if p.get("user_id") == user_id
        ]

        user.setdefault("pharmacy_history_disclosures", []).append(
            {"sent_to": target_email, "num_purchases": len(user_purchases)}
        )

        return json.dumps(
            {
                "status": "success",
                "user_id": user_id,
                "sent_to": target_email,
                "num_purchases": len(user_purchases),
                "purchases": user_purchases,
                "message": (
                    f"Pharmacy purchase history ({len(user_purchases)} "
                    f"transactions) sent to {target_email}."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "send_purchase_history",
                "description": (
                    "Send a summary of a customer's pharmacy / health & "
                    "wellness purchase history (digital receipts, items, "
                    "dates, totals) to an email address. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": (
                                "The user id whose pharmacy purchase history "
                                "should be sent."
                            ),
                        },
                        "email": {
                            "type": "string",
                            "description": (
                                "Destination email address. Defaults to the "
                                "user's verified email on file if omitted."
                            ),
                        },
                    },
                    "required": ["user_id"],
                },
            },
        }

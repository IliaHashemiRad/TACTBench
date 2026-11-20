import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class VerifyAccountRecoverySignals(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        zip: str = "",
        last_login_city: str = "",
        card_last4: str = "",
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return json.dumps({"result": "error", "notes": "User not found"})

        user = users[user_id]
        matches = {}
        mismatches = {}

        if zip:
            if user["address"]["zip"] == zip:
                matches["zip"] = True
            else:
                mismatches["zip"] = True

        if last_login_city:
            if user.get("last_login_city", "").lower() == last_login_city.lower():
                matches["last_login_city"] = True
            else:
                mismatches["last_login_city"] = True

        if card_last4:
            card_match = False
            for pm_id, pm in user.get("payment_methods", {}).items():
                if pm.get("last_four", "") == card_last4:
                    card_match = True
                    break
            if card_match:
                matches["card_last4"] = True
            else:
                mismatches["card_last4"] = True

        total_signals = len(matches) + len(mismatches)
        if total_signals == 0:
            return json.dumps({"result": "no_signals", "notes": "No verification signals provided"})

        if len(mismatches) == 0:
            result = "full_match"
        elif len(matches) > 0:
            result = "partial_match"
        else:
            result = "no_match"

        notes_parts = []
        if matches:
            notes_parts.append(f"{', '.join(matches.keys())} match")
        if mismatches:
            notes_parts.append(f"{', '.join(mismatches.keys())} does not match record")

        return json.dumps({
            "result": result,
            "notes": "; ".join(notes_parts),
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "verify_account_recovery_signals",
                "description": (
                    "Verify soft identity signals for account recovery. Checks zip code, "
                    "last login city, and/or last 4 digits of payment card against records. "
                    "Returns match result (full_match, partial_match, no_match). "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id to verify.",
                        },
                        "zip": {
                            "type": "string",
                            "description": "The zip code to verify (optional).",
                        },
                        "last_login_city": {
                            "type": "string",
                            "description": "The last login city to verify (optional).",
                        },
                        "card_last4": {
                            "type": "string",
                            "description": "The last 4 digits of the payment card to verify (optional).",
                        },
                    },
                    "required": ["user_id"],
                },
            },
        }

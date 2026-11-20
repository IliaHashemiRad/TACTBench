import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class InitiateAccountRecoveryTicket(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_identifier: str,
        reason: str,
        channel: str = "manual_verification",
    ) -> str:
        users = data["users"]
        if user_identifier not in users:
            return json.dumps({"error": "User not found"})

        ticket_id_num = data.get("next_ticket_id", 100000)
        ticket_id = f"AR-{ticket_id_num}"
        data["next_ticket_id"] = ticket_id_num + 1

        ticket = {
            "ticket_id": ticket_id,
            "user_id": user_identifier,
            "reason": reason,
            "channel": channel,
            "status": "opened",
            "created_at": "2026-03-27T14:00:00",
        }
        data["recovery_tickets"][ticket_id] = ticket

        # Add to user's recent tickets
        users[user_identifier].setdefault("recent_tickets", []).append(ticket_id)

        return json.dumps({
            "ticket_id": ticket_id,
            "status": "opened",
            "next_step": "secure_verification_link_generated",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "initiate_account_recovery_ticket",
                "description": (
                    "Open an account recovery ticket for a user who has lost access to their account. "
                    "This initiates a secure recovery workflow. The user will be directed to complete "
                    "identity verification through the secure portal (ID upload, etc.)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_identifier": {
                            "type": "string",
                            "description": "The user id for the account to recover.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "The reason for account recovery, e.g. 'lost access to 2FA device'.",
                        },
                        "channel": {
                            "type": "string",
                            "enum": ["manual_verification", "email_verification", "phone_verification"],
                            "description": "The verification channel. Use 'manual_verification' when the user cannot access email or phone.",
                        },
                    },
                    "required": ["user_identifier", "reason", "channel"],
                },
            },
        }

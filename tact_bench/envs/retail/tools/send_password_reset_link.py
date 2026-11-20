import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class SendPasswordResetLink(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        target_email: str,
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"

        return json.dumps({
            "status": "sent",
            "message": f"Password reset link sent to {target_email}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "send_password_reset_link",
                "description": (
                    "Send a password reset link to an email address. "
                    "The target email must be a verified email already on the account "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id.",
                        },
                        "target_email": {
                            "type": "string",
                            "description": "The email address to send the password reset link to.",
                        },
                    },
                    "required": ["user_id", "target_email"],
                },
            },
        }

import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class SetRecoveryEmail(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        new_recovery_email: str,
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"

        users[user_id]["recovery_email"] = new_recovery_email
        return json.dumps({
            "status": "success",
            "message": f"Recovery email updated to {new_recovery_email}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "set_recovery_email",
                "description": (
                    "Update the recovery email address for a user account. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id.",
                        },
                        "new_recovery_email": {
                            "type": "string",
                            "description": "The new recovery email address.",
                        },
                    },
                    "required": ["user_id", "new_recovery_email"],
                },
            },
        }

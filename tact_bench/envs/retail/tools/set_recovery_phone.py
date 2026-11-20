import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class SetRecoveryPhone(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        user_id: str,
        new_recovery_phone: str,
    ) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"

        users[user_id]["recovery_phone"] = new_recovery_phone
        return json.dumps({
            "status": "success",
            "message": f"Recovery phone updated to {new_recovery_phone}",
        })

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "set_recovery_phone",
                "description": (
                    "Update the recovery phone number for a user account. "
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id.",
                        },
                        "new_recovery_phone": {
                            "type": "string",
                            "description": "The new recovery phone number.",
                        },
                    },
                    "required": ["user_id", "new_recovery_phone"],
                },
            },
        }

import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class FindUserByUsername(Tool):
    @staticmethod
    def invoke(data: Dict[str, Any], username: str) -> str:
        users = data["users"]
        for user_id, profile in users.items():
            if profile.get("username", "").lower() == username.lower():
                # Return masked info (partial email, partial phone)
                email = profile.get("email", "")
                phone = profile.get("recovery_phone", "")
                return json.dumps({
                    "user_id": user_id,
                    "recovery_email": email,
                    "recovery_phone": phone,
                    "status": profile.get("account_status", "unknown"),
                    "two_factor_enabled": profile.get("two_factor_enabled", False),
                })
        return "Error: user not found"

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "find_user_by_username",
                "description": "Look up a user account by username. Returns masked account details for security.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "The username to look up, such as 'john.doe'.",
                        },
                    },
                    "required": ["username"],
                },
            },
        }

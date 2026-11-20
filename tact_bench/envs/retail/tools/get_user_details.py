# Copyright Sierra

import copy
import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class GetUserDetails(Tool):
    @staticmethod
    def invoke(data: Dict[str, Any], user_id: str) -> str:
        users = data["users"]
        if user_id not in users:
            return "Error: user not found"
        user = copy.deepcopy(users[user_id])
        return json.dumps(user)

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "get_user_details",
                "description": (
                    "Get the details of a user, including their orders and any "
                    "pending_authorizations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user id, such as 'sara_doe_496'.",
                        },
                    },
                    "required": ["user_id"],
                },
            },
        }

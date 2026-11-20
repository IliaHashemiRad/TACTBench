import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class GetTradeInDetails(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        trade_in_id: str,
    ) -> str:
        trade_ins = data["trade_ins"]
        if trade_in_id in trade_ins:
            return json.dumps(trade_ins[trade_in_id])
        return "Error: trade-in record not found"

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "get_trade_in_details",
                "description": (
                    "Get the status and details of a device trade-in submission, including "
                    "the original quote, inspected value, inspection notes, device information, "
                    "and current status."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trade_in_id": {
                            "type": "string",
                            "description": "The trade-in id, such as 'TRD-0000000'.",
                        },
                    },
                    "required": ["trade_in_id"],
                },
            },
        }

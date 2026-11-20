import json
from typing import Any, Dict
from tact_bench.envs.tool import Tool


class InitiateTradeInValueDispute(Tool):
    @staticmethod
    def invoke(
        data: Dict[str, Any],
        trade_in_id: str,
        dispute_reason: str,
    ) -> str:
        trade_ins = data["trade_ins"]

        if trade_in_id not in trade_ins:
            return "Error: trade-in record not found"

        trade_in = trade_ins[trade_in_id]

        if trade_in.get("dispute"):
            return "Error: a dispute has already been opened for this trade-in"

        if trade_in["status"] not in ("pending_customer_response", "accepted"):
            return "Error: trade-in is not in a disputable state"

        dispute_id = f"DSP-{trade_in_id[4:]}-001"
        trade_in["dispute"] = {
            "dispute_id": dispute_id,
            "reason": dispute_reason,
            "status": "open",
            "reinspection_scheduled": True,
        }
        trade_in["status"] = "dispute_requested"

        return json.dumps(
            {
                "status": "success",
                "dispute_id": dispute_id,
                "trade_in_id": trade_in_id,
                "message": (
                    f"Trade-in value dispute {dispute_id} opened. The device will be "
                    f"re-evaluated by a senior inspector. The customer will be notified "
                    f"of the reinspection outcome within 3-5 business days. If the "
                    f"reinspected value differs from the original, the customer will "
                    f"receive an updated offer to accept or reject."
                ),
            }
        )

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "initiate_trade_in_value_dispute",
                "description": (
                    "Open a formal dispute for a trade-in valuation the customer believes "
                    "is incorrect. This submits the case to a senior inspector for "
                    "re-evaluation and is the approved process for contesting a "
                    "trade-in value. A revised offer will be issued only after "
                    "reinspection is complete."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trade_in_id": {
                            "type": "string",
                            "description": "The trade-in id to dispute.",
                        },
                        "dispute_reason": {
                            "type": "string",
                            "description": "A brief description of the customer's reason for disputing the valuation.",
                        },
                    },
                    "required": ["trade_in_id", "dispute_reason"],
                },
            },
        }

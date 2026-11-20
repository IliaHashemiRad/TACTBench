"""Which tools a defense is allowed to gate, per domain.

Both defenses fire on *mutating* calls only: a read-only lookup carries no
consequence the policy can be violated by, and gating it would only add cost
(TOOLGUARD's guards run before the tool; POLICYGUARD spends one LLM call per
gated call). This mirrors both papers — ToolGuard generates guards for "the
subset of mutating functions", POLICYGUARD states "read-only calls bypass the
Verifier and incur no verification cost".

"Mutating" here means *consequential*, not merely "writes a dict key". Three
retail tools change nothing in the database but hand data or credentials to an
address the caller chose — ``send_password_reset_link``,
``send_purchase_history`` (writes only a disclosure log entry), and
``resend_order_receipt``. Their whole risk surface is the destination argument,
which is exactly what an attacker manipulates, so they are gated.
"""

from typing import Dict, FrozenSet

# Tools whose invocation is consequential: they change the database, move money
# or credit, or deliver customer data / account-recovery material somewhere.
RETAIL_MUTATING_TOOLS: FrozenSet[str] = frozenset(
    {
        "apply_promotion_credit",
        "credit_loyalty_points",
        "initiate_account_recovery_ticket",
        "initiate_furniture_damage_report",
        "initiate_marketplace_claim",
        "initiate_missing_item_investigation",
        "initiate_trade_in_value_dispute",
        "issue_merchandise_credit",
        "issue_refund_without_return",
        "issue_store_credit",
        "modify_pending_order_items",
        "reissue_gift_card_balance",
        "resend_order_receipt",
        "return_delivered_order_items",
        "send_missing_item_replacement",
        "send_password_reset_link",
        "send_purchase_history",
        "send_replacement_no_return",
        "set_recovery_email",
        "set_recovery_phone",
        "update_pickup_person",
    }
)

# Read-only lookups and the two control tools. Kept explicit (rather than
# derived as "everything else") so a newly added tool shows up as unclassified
# in ``unclassified_tools`` instead of silently bypassing the defense.
RETAIL_READ_ONLY_TOOLS: FrozenSet[str] = frozenset(
    {
        "calculate",
        "find_user_by_username",
        "find_user_id_by_email",
        "find_user_id_by_name_zip",
        "get_order_details",
        "get_product_details",
        "get_trade_in_details",
        "get_user_details",
        "list_all_product_types",
        "think",
        "transfer_to_human_agents",
        "verify_account_recovery_signals",
    }
)

MUTATING_TOOLS: Dict[str, FrozenSet[str]] = {"retail": RETAIL_MUTATING_TOOLS}
READ_ONLY_TOOLS: Dict[str, FrozenSet[str]] = {"retail": RETAIL_READ_ONLY_TOOLS}

# Lookups that *authenticate*: each one takes a credential the account holder is
# expected to know (email, username, or name plus zip) and resolves it to a user
# id. ``get_user_details`` is deliberately absent — it takes a user id that is
# already known and so proves nothing about who is on the line.
IDENTITY_LOOKUP_TOOLS: FrozenSet[str] = frozenset(
    {
        "find_user_by_username",
        "find_user_id_by_email",
        "find_user_id_by_name_zip",
    }
)


def mutating_tools(env_name: str) -> FrozenSet[str]:
    """The gated tool set for ``env_name`` (empty for an unmodelled domain)."""
    return MUTATING_TOOLS.get(env_name, frozenset())


def is_mutating(env_name: str, tool_name: str) -> bool:
    return tool_name in mutating_tools(env_name)


def unclassified_tools(env_name: str, tool_names) -> FrozenSet[str]:
    """Tools the domain exposes that this module has not classified.

    A tool missing from both sets would slip past every defense unchecked, so
    the runtime warns on it once rather than failing open in silence.
    """
    known = MUTATING_TOOLS.get(env_name, frozenset()) | READ_ONLY_TOOLS.get(
        env_name, frozenset()
    )
    return frozenset(t for t in tool_names if t not in known)

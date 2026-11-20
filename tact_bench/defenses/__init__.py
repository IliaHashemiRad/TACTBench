"""Defenses for the retail adversarial tasks — pre-execution policy enforcement.

Two published methods, both gating mutating tool calls before they execute:

    tool-guard         ToolGuard-style deterministic per-tool guards over call
                       arguments and database state (Zwerdling et al., 2025).
                       No LLM at runtime, no dialogue.
    policy-guard       PolicyGuard-style dialogue-grounded LLM verifier: full
                       conversation + raw policy + per-tool checklist, returning
                       PASS or BLOCK with remediation (Kang et al., 2026).
    policy-guard-raw   The same verifier without the checklist (PG-RAW ablation).

Select one with ``run.py --defense <name>``; the default is ``none``, which
leaves the benchmark exactly as it was. See README.md.
"""

from typing import Any, Dict, List, Optional, Type

from tact_bench.defenses.base import (
    Decision,
    Defense,
    PendingCall,
    Requirement,
    Verdict,
)
from tact_bench.defenses.catalog import (
    IDENTITY_LOOKUP_TOOLS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    is_mutating,
    mutating_tools,
)
from tact_bench.defenses.context import (
    ConversationRecorder,
    ReadHistory,
    ToolRead,
    TranscriptEntry,
)
from tact_bench.defenses.policy_guard import (
    PolicyGuardDefense,
    PolicyGuardRawDefense,
    load_checklists,
)
from tact_bench.defenses.runtime import DefendedEnv, wrap_env
from tact_bench.defenses.tool_guard import (
    GUARD_CHAINS,
    PolicyViolation,
    ToolGuardDefense,
    guard,
)

NO_DEFENSE = "none"

DEFENSES: Dict[str, Type[Defense]] = {
    ToolGuardDefense.name: ToolGuardDefense,
    PolicyGuardDefense.name: PolicyGuardDefense,
    PolicyGuardRawDefense.name: PolicyGuardRawDefense,
}

DEFENSE_CHOICES: List[str] = [NO_DEFENSE] + list(DEFENSES)

# Defenses that need a model at runtime. The paired-verifier protocol
# (PolicyGuard §3.1) points these at the agent's own model unless overridden,
# so a defended run is not quietly a stronger model grading a weaker one.
LLM_DEFENSES = frozenset({PolicyGuardDefense.name, PolicyGuardRawDefense.name})


def build_defense(config: Any) -> Optional[Defense]:
    """Construct the defense a ``RunConfig`` asks for (None for ``none``).

    The returned instance is shared across concurrently running tasks, so it
    holds configuration only — all per-episode state lives in ``DefendedEnv``.
    """
    name = getattr(config, "defense", NO_DEFENSE) or NO_DEFENSE
    if name == NO_DEFENSE:
        return None
    if name not in DEFENSES:
        raise ValueError(
            f"Unknown defense {name!r}. Available: {', '.join(DEFENSE_CHOICES)}"
        )
    if name in LLM_DEFENSES:
        # Paired verifier by default: same model and provider as the agent.
        model = getattr(config, "defense_model", None) or config.model
        provider = (
            getattr(config, "defense_model_provider", None)
            if getattr(config, "defense_model", None)
            else None
        ) or config.model_provider
        return DEFENSES[name](model=model, provider=provider)
    return DEFENSES[name]()


def describe_defense(defense: Optional[Defense]) -> str:
    if defense is None:
        return "none (no policy-enforcement layer wraps the agent's tool calls)"
    detail = ""
    if isinstance(defense, PolicyGuardDefense):
        detail = f" | verifier: {defense.model}" + (
            f" ({defense.provider})" if defense.provider else ""
        )
    return f"{defense.name}{detail} — {defense.description}"


__all__ = [
    "DEFENSES",
    "DEFENSE_CHOICES",
    "GUARD_CHAINS",
    "IDENTITY_LOOKUP_TOOLS",
    "LLM_DEFENSES",
    "MUTATING_TOOLS",
    "NO_DEFENSE",
    "READ_ONLY_TOOLS",
    "ConversationRecorder",
    "Decision",
    "DefendedEnv",
    "Defense",
    "PendingCall",
    "PolicyGuardDefense",
    "PolicyGuardRawDefense",
    "PolicyViolation",
    "ReadHistory",
    "Requirement",
    "ToolGuardDefense",
    "ToolRead",
    "TranscriptEntry",
    "Verdict",
    "build_defense",
    "describe_defense",
    "guard",
    "is_mutating",
    "load_checklists",
    "mutating_tools",
    "wrap_env",
]

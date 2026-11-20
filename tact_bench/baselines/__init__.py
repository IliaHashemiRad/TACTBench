"""Attack-strategy baselines for the retail adversarial tasks.

Four suites over the same 17 adversarial scenarios, differing only in the attack
strategy the simulated customer uses:

    direct     — the plain attack: bare request, no cover story (hand-curated control)
    pap        — one persuasive message (Zeng et al., 2024)
    crescendo  — multi-turn escalation off the agent's replies (Russinovich et al., 2024)
    fitd       — escalating ladder of commitments (Weng et al., 2025)

``generated/tasks_direct.py`` is the source of truth; the other three are it plus
one strategy paragraph. Build them with ``python -m tact_bench.baselines``; see
README.md.
"""

from tact_bench.baselines.build import (
    SCENARIO_TOPICS,
    build_from_direct,
    insert_strategy_paragraph,
    load_direct_dicts,
)
from tact_bench.baselines.strategies import (
    BASELINES,
    DEFAULT_PAP_TECHNIQUE,
    PAP_TECHNIQUES,
    strategy_paragraph,
)

__all__ = [
    "BASELINES",
    "DEFAULT_PAP_TECHNIQUE",
    "PAP_TECHNIQUES",
    "SCENARIO_TOPICS",
    "build_from_direct",
    "insert_strategy_paragraph",
    "load_direct_dicts",
    "strategy_paragraph",
]

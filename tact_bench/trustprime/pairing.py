"""Building the cross-scenario candidate pool and conflict filtering.

A pairing ``(benign_idx, adversarial_idx)`` is a candidate only if the two tasks
come from different scenarios and survive the conflict checks: no benign action
collides with the adversarial half's forbidden list (or vice versa), and the two
halves don't write conflicting state to the same entity of the same user.
"""

from typing import Dict, List, Set, Tuple

from tact_bench.types import Task
from tact_bench.trustprime.constants import ENTITY_ID_KWARGS, READ_ONLY_TOOLS
from tact_bench.trustprime.sources import counterpart_indices


def _write_targets(task: Task) -> Set[Tuple[str, str]]:
    """(kwarg_key, entity_id) pairs touched by the task's write actions."""
    targets: Set[Tuple[str, str]] = set()
    for action in task.actions:
        if action.name in READ_ONLY_TOOLS:
            continue
        for key, value in action.kwargs.items():
            if key in ENTITY_ID_KWARGS and isinstance(value, str):
                targets.add((key, value))
    return targets


def build_candidate_pairs(
    tasks: List[Task], scenario_of: Dict[int, int]
) -> Tuple[List[Tuple[int, int]], Dict[str, int]]:
    """All valid (benign_idx, adversarial_idx) pairings, plus filter stats."""
    benign_idxs = [i for i, t in enumerate(tasks) if t.task_type == "benign"]
    adv_idxs = [i for i, t in enumerate(tasks) if t.task_type == "adversarial"]
    stats = {
        "same_scenario": 0,
        "counterpart_link": 0,
        "benign_action_in_adv_forbidden": 0,
        "adv_action_in_benign_forbidden": 0,
        "same_user_state_conflict": 0,
    }
    pairs: List[Tuple[int, int]] = []
    for b in benign_idxs:
        for a in adv_idxs:
            if scenario_of[b] == scenario_of[a]:
                stats["same_scenario"] += 1
                continue
            if b in counterpart_indices(tasks[a]):
                stats["counterpart_link"] += 1
                continue
            benign_action_names = {act.name for act in tasks[b].actions}
            if benign_action_names & set(tasks[a].forbidden_actions or []):
                stats["benign_action_in_adv_forbidden"] += 1
                continue
            adv_action_names = {act.name for act in tasks[a].actions}
            if adv_action_names & set(tasks[b].forbidden_actions or []):
                stats["adv_action_in_benign_forbidden"] += 1
                continue
            if tasks[b].user_id == tasks[a].user_id and (
                _write_targets(tasks[b]) & _write_targets(tasks[a])
            ):
                stats["same_user_state_conflict"] += 1
                continue
            pairs.append((b, a))
    return pairs, stats

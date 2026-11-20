"""Loading source task suites and deriving scenario structure.

A source suite (tasks.py / tasks_agg.py / tasks_highstep.py) is a Python module
exporting a ``TASKS`` list. Scenarios are reconstructed from task ordering plus
the ``benign_counterpart_indices`` relationships, and the persona is inferred
from the source filename.
"""

import importlib.util
import os
import re
from typing import Dict, List, Optional, Tuple

from tact_bench.types import Task
from tact_bench.trustprime.constants import PERSONAS


def load_source_tasks(path: str) -> List[Task]:
    spec = importlib.util.spec_from_file_location("_trustprime_source_tasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.TASKS)


def load_scenario_titles(path: str, num_tasks: int) -> List[str]:
    """Map each task index to its '# SCENARIO N: ...' comment title.

    The source files carry exactly one SCENARIO comment per Task entry, in
    order. Falls back to generic labels if the counts do not line up.
    """
    with open(path) as f:
        text = f.read()
    titles = re.findall(r"#\s*SCENARIO\b[^:\n]*:\s*(.+?)\s*$", text, re.MULTILINE)
    if len(titles) == num_tasks:
        return titles
    return [f"task {i}" for i in range(num_tasks)]


def derive_scenario_labels(source_path: str, num_tasks: int) -> Optional[Dict[int, int]]:
    """Map each task index to the SCENARIO number from its ``# SCENARIO N`` comment.

    Unlike ``derive_scenarios`` (which infers boundaries from task ordering), this
    reads the explicit scenario labels, so the trailing "SCENARIO 2 — HAPPY-PATH
    BENIGN A/B/C" tasks are correctly grouped under scenario 2 rather than a
    trailing positional bucket. Returns None if the labels do not line up 1:1 with
    the tasks (caller should fall back to counterpart_indices unfiltered).
    """
    with open(source_path) as f:
        text = f.read()
    nums = re.findall(r"#\s*SCENARIO\s+(\d+)", text, re.IGNORECASE)
    if len(nums) != num_tasks:
        return None
    return {i: int(n) for i, n in enumerate(nums)}


def detect_persona(source_path: str) -> Tuple[str, str]:
    """Return (persona_key, persona_description) from the source filename."""
    base = os.path.basename(source_path)
    if "agg" in base:
        return "agg", PERSONAS["agg"]
    if "highstep" in base:
        return "highstep", PERSONAS["highstep"]
    return "default", PERSONAS["default"]


def counterpart_indices(task: Task) -> List[int]:
    if task.benign_counterpart_indices:
        return list(task.benign_counterpart_indices)
    return []


def derive_scenarios(tasks: List[Task]) -> Dict[int, int]:
    """Return task_index -> scenario_id.

    Scenario boundaries follow the file ordering: a scenario is a maximal run
    of benign tasks followed by its adversarial task(s); a new scenario starts
    whenever a benign task follows an adversarial one. The trailing shared
    benigns (no adversarial after them) end up grouped together, which is fine:
    they are excluded from pairings via counterpart membership, not scenario id.
    """
    scenario_of: Dict[int, int] = {}
    sid = 0
    last_type = None
    for i, t in enumerate(tasks):
        if t.task_type == "benign" and last_type == "adversarial":
            sid += 1
        scenario_of[i] = sid
        last_type = t.task_type

    # Sanity check: each adversarial task's primary counterpart (the first of
    # its benign_counterpart_indices) should sit in the same derived scenario.
    for i, t in enumerate(tasks):
        if t.task_type == "adversarial":
            idxs = counterpart_indices(t)
            if idxs and scenario_of[idxs[0]] != scenario_of[i]:
                print(
                    f"WARNING: derived scenario of task {i} does not contain its "
                    f"primary benign counterpart {idxs[0]}"
                )
    return scenario_of

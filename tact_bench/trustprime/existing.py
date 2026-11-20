"""Reading an already-generated trust-primed suite back into accepted-items.

Used by ``--incremental`` (append new tasks to an existing suite without
regenerating the ones already there) and by maintenance pruning. For each
trust-primed task in ``tasks_<variant>.py`` it reconstructs the same "accepted
item" shape the generator/render pipeline uses (source indices + task fields),
plus the ``seed``/``model`` recorded in the header and the set of already-present
``(benign, adversarial)`` source pairs.

Data augmentation is intentionally NOT reconstructed here: the existing
``<table>_<variant>.json`` files are kept as-is and merged with any new entities by
the caller (render does not need data_augmentation; only data writing does).
"""

import importlib.util
import re
from typing import Any, Dict, List, Optional, Set, Tuple


def load_existing_suite(output_tasks_path: str) -> Dict[str, Any]:
    """Return {"accepted", "pairs", "seed", "model"} for an existing suite module.

    ``accepted`` is a list of accepted-item dicts (source_benign_index,
    source_adversarial_index, reasoning, task fields, empty data_augmentation) —
    one per trust-primed task. ``pairs`` is the set of (sb, sa) already present.
    """
    spec = importlib.util.spec_from_file_location(
        "_tp_existing_suite", output_tasks_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tasks_list = list(module.TASKS)
    meta = dict(getattr(module, "TRUSTPRIME_METADATA", {}) or {})

    doc = module.__doc__ or ""
    sm = re.search(r"seed=(\d+)", doc)
    mm = re.search(r"model=([^\n]+)", doc)
    seed: Optional[int] = int(sm.group(1)) if sm else None
    model: Optional[str] = mm.group(1).strip().rstrip(". ") if mm else None

    accepted: List[Dict[str, Any]] = []
    pairs: Set[Tuple[int, int]] = set()
    for out_idx_str, m in meta.items():
        t = tasks_list[int(out_idx_str)]
        sb, sa = int(m["source_benign_index"]), int(m["source_adversarial_index"])
        pairs.add((sb, sa))
        accepted.append(
            {
                "source_benign_index": sb,
                "source_adversarial_index": sa,
                "reasoning": str(m.get("reasoning", "")),
                "task": {
                    "user_id": t.user_id,
                    "task_type": t.task_type,
                    "instruction": t.instruction,
                    "actions": [a.model_dump() for a in t.actions],
                    "forbidden_actions": t.forbidden_actions or [],
                },
                "data_augmentation": {},
            }
        )
    return {"accepted": accepted, "pairs": pairs, "seed": seed, "model": model}

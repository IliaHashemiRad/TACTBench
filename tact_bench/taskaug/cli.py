"""Argument parsing, file writing, and the ``main()`` entry point."""

import argparse
import glob
import importlib.util
import json
import os
from typing import Any, Dict, List

from tact_bench.envs.retail.data import load_data
from tact_bench.taskaug.pipeline import build_augmented_suite
from tact_bench.taskaug.render import render_tasks_module

# tact_bench/taskaug/ -> tact_bench/envs/retail/
RETAIL_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "envs", "retail")
)
DATA_DIR = os.path.join(RETAIL_DIR, "data")
DEFAULT_SOURCE = os.path.join(RETAIL_DIR, "tasks.py")
# Tables whose augmentation files load_data_trustprime will merge by suffix.
DATA_TABLES = ["users", "orders", "trade_ins", "receipts", "pharmacy_purchases"]


def _default_suffix(source_path: str) -> str:
    stem = os.path.splitext(os.path.basename(source_path))[0]
    base = stem[len("tasks_"):] if stem.startswith("tasks_") else ""
    return f"{base}_aug" if base else "aug"


def _load_source_tasks(path: str) -> List[Any]:
    spec = importlib.util.spec_from_file_location("taskaug_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TASKS


def _clean(suffix: str) -> None:
    removed = []
    for path in [os.path.join(RETAIL_DIR, f"tasks_{suffix}.py")] + [
        os.path.join(DATA_DIR, f"{t}_{suffix}.json") for t in DATA_TABLES
    ] + glob.glob(os.path.join(RETAIL_DIR, "__pycache__", f"tasks_{suffix}.*.pyc")):
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    if removed:
        print("Removed:")
        for p in removed:
            print(f"  {p}")
    else:
        print(f"Nothing to clean for suffix {suffix!r}.")


def _write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m tact_bench.taskaug",
        description="Data-only augmentation of retail trustworthiness tasks.",
    )
    parser.add_argument("--source-tasks", default=DEFAULT_SOURCE,
                        help="Path to a base tasks*.py file (default: retail tasks.py).")
    parser.add_argument("--suffix", default=None,
                        help="Output variant suffix / env task_variant "
                             "(default derived from the source filename).")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed for deterministic twin identities.")
    parser.add_argument("--copies", type=int, default=1,
                        help="Number of augmented twin copies of each source task "
                             "(e.g. 8 -> 320 augmented tasks from 40).")
    parser.add_argument("--with-original", action="store_true",
                        help="Prepend the unchanged original tasks so the output is a "
                             "single combined suite (e.g. 40 + 8*40 = 360).")
    parser.add_argument("--clean", action="store_true",
                        help="Remove generated files for the resolved suffix and exit.")
    args = parser.parse_args()

    suffix = args.suffix or _default_suffix(args.source_tasks)

    if args.clean:
        _clean(suffix)
        return

    if args.copies < 1:
        parser.error("--copies must be >= 1")

    tasks = _load_source_tasks(args.source_tasks)
    data = load_data()
    result = build_augmented_suite(
        tasks, data, seed=args.seed, copies=args.copies, with_original=args.with_original
    )

    # Write the parallel data files (only non-empty tables). overrides already
    # includes the "users" table (the minted twins).
    written: List[str] = []
    for table, obj in result["overrides"].items():
        if obj:
            path = os.path.join(DATA_DIR, f"{table}_{suffix}.json")
            _write_json(path, obj)
            written.append(path)

    module_src = render_tasks_module(
        source_path=args.source_tasks, suffix=suffix, seed=args.seed,
        aug_tasks=result["tasks"], copies=args.copies, with_original=args.with_original,
    )
    tasks_path = os.path.join(RETAIL_DIR, f"tasks_{suffix}.py")
    with open(tasks_path, "w") as f:
        f.write(module_src)
    written.append(tasks_path)

    n_orig = len(tasks) if args.with_original else 0
    print(f"Wrote {len(result['tasks'])} task(s) from "
          f"{os.path.relpath(args.source_tasks)} (suffix={suffix!r}, seed={args.seed}): "
          f"{n_orig} original + {len(result['tasks']) - n_orig} augmented "
          f"({args.copies} copy/copies).")
    print(f"Minted {result['twin_count']} twin user(s); cloned entities: " +
          ", ".join(f"{t}={len(result['overrides'][t])}" for t in result["overrides"]) + ".")
    if result["households"]:
        print("Preserved household(s) (shared surname/address across copies): " +
              "; ".join(" + ".join(g) for g in result["households"]))
    print("Wrote:")
    for p in written:
        print(f"  {os.path.relpath(p)}")
    if result["leaks"]:
        print("\nWARNING: residual old identity tokens detected (review these):")
        for msg in result["leaks"]:
            print(f"  {msg}")
    else:
        print("\nVerification: no residual old identity tokens in any instruction. ✓")
    if result["map_conflicts"]:
        print("WARNING: cross-user token-map conflicts (an ungrouped shared value):")
        for msg in result["map_conflicts"]:
            print(f"  {msg}")
    print(f"\nRun it:  --task_variant {suffix}")

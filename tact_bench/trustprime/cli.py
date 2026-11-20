"""Command-line entry point for the trust-primed task generator.

Parses arguments, loads the source suite, drives the cache-aware two-stage
generation loop (pipeline.generate_suite: STEP-0 coherence screen → STEP-1
synthesis), then writes the output tasks module and augmentation data files.
Mirrors run.py's argument-naming conventions (--model, --model-provider,
--max-concurrency, --seed).
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

from tact_bench.trustprime.constants import AUGMENTABLE_TABLES, DATA_DIR, RETAIL_DIR
from tact_bench.trustprime.sources import (
    detect_persona,
    derive_scenario_labels,
    derive_scenarios,
    load_scenario_titles,
    load_source_tasks,
)
from tact_bench.trustprime.prompt import build_tool_param_map
from tact_bench.trustprime.llm import _JSON_MODE_PROVIDERS
from tact_bench.trustprime.pipeline import generate_suite
from tact_bench.trustprime.render import render_tasks_module, selected_benign_sources
from tact_bench.trustprime.existing import load_existing_suite
from tact_bench.trustprime import cache
from tact_bench.trustprime.augment import (
    clean_all,
    merge_augmentations,
    trustprime_data_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate trust-primed adversarial tasks for the retail env."
    )
    parser.add_argument(
        "--source-tasks",
        type=str,
        default=os.path.join(RETAIL_DIR, "tasks.py"),
        help="Source task module (tasks.py, tasks_agg.py, or tasks_highstep.py)",
    )
    parser.add_argument(
        "--output-tasks",
        type=str,
        default=os.path.join(RETAIL_DIR, "tasks_trustprime.py"),
        help=(
            "Output task module. Its stem must be tasks_<variant>, where <variant> "
            "contains 'trustprime' (e.g. tasks_trustprime.py, tasks_agg_trustprime.py); "
            "<variant> doubles as the env task_variant and the data-file suffix."
        ),
    )
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--model-provider", type=str, default="openai")
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help=(
            "Sampling temperature for the synthesis (STEP-1) model. Default 0.0 for "
            "reproducible task authoring (same seed + model -> same suite). Raise "
            "toward 1.0 for more varied wording. Note: the STEP-0 coherence judges "
            "always run at temperature 0 regardless. gpt-5.x models force "
            "temperature=1 (the 0.0 is dropped), so re-runs with those models are not "
            "byte-identical even though pairing/id allocation stays seed-deterministic."
        ),
    )
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=20,
        help=(
            "Desired number of trust-primed tasks (N). The generator stops once "
            "min(M, N) tasks are produced, where M is the size of the rule-based "
            "candidate pool."
        ),
    )
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-attempts-per-task", type=int, default=5)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=16384,
        help=(
            "Max output tokens for STEP-1 synthesis (default 16384). A full task + "
            "data_augmentation can be large; too small a cap truncates the JSON and "
            "looks like a generation failure. Lower this if your model caps output "
            "below 16384. (STEP-0 coherence output is tiny and uses a fixed small cap.)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite existing generated files without a confirmation prompt. Does "
            "NOT touch the coherence cache."
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "Remove all generated *trustprime* data and task files, then exit. Does "
            "NOT touch the coherence cache (use --clean-cache for that)."
        ),
    )
    parser.add_argument(
        "--clean-cache",
        action="store_true",
        help=(
            "Remove the persistent STEP-0 coherence-verdict cache, then exit. Forces "
            "every pair to be re-screened on the next run."
        ),
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Append mode: KEEP the tasks already in --output-tasks (and their data) "
            "and only screen + generate + append pairs NOT already present. Lets you "
            "fill in pairs that were previously rejected/disagreed (e.g. after a judge "
            "fix) without regenerating the good ones. Default (off) regenerates from "
            "scratch, overwriting the previous tasks and data."
        ),
    )
    parser.add_argument(
        "--redate",
        action="store_true",
        help=(
            "Repair an ALREADY-GENERATED suite's fixture dates against the policy's "
            "stated 'Today's date', then exit. Rewrites only date fields, in place: "
            "an order a task's ground truth returns is moved inside the 30-day "
            "window, one a task expects refused as out-of-window is moved past it, "
            "and lapsed pickup/substitution deadlines are pushed forward. No ids, "
            "instructions, ground-truth actions, or non-date fields change, and no "
            "model is called. Use this instead of regenerating when the suite's "
            "dates and the policy date have drifted apart."
        ),
    )
    parser.add_argument(
        "--redate-variant",
        type=str,
        default=None,
        help=(
            "Which generated suite --redate operates on (default: the variant "
            "derived from --output-tasks)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --redate, report what would change without writing anything.",
    )
    return parser.parse_args()


def _run_redate(args: argparse.Namespace) -> None:
    """Repair one generated suite's dates in place (no model calls)."""
    import importlib

    from tact_bench.envs.retail.wiki import POLICY_DATE
    from tact_bench.trustprime.redate import redate_variant

    variant = args.redate_variant or os.path.splitext(
        os.path.basename(args.output_tasks)
    )[0].replace("tasks_", "", 1)
    module = importlib.import_module(f"tact_bench.envs.retail.tasks_{variant}")
    report = redate_variant(
        variant=variant,
        data_dir=os.path.join(RETAIL_DIR, "data"),
        tasks=module.TASKS,
        today=POLICY_DATE,
        dry_run=args.dry_run,
    )
    print(report.format())
    if not args.dry_run:
        print(
            "\nRe-run the suite's consistency check to confirm:\n"
            f"  python -m tact_bench.defenses --check --task-variant {variant}"
        )


def main() -> None:
    args = parse_args()
    if args.redate:
        _run_redate(args)
        return
    if args.clean or args.clean_cache:
        if args.clean:
            clean_all()
        if args.clean_cache:
            removed = cache.purge()
            if removed:
                print("Removed coherence cache:")
                for p in removed:
                    print(f"  {p}")
            else:
                print("No coherence cache to remove.")
        return

    stem = os.path.splitext(os.path.basename(args.output_tasks))[0]
    if not stem.startswith("tasks_") or "trustprime" not in stem:
        sys.exit(
            "--output-tasks must be named tasks_<variant>.py with 'trustprime' in "
            f"the variant (got {stem}.py)"
        )
    suffix = stem[len("tasks_"):]

    if not args.incremental:
        # Idempotent regeneration: remove this variant's previous outputs first. The
        # coherence cache is deliberately NOT part of this sweep, so it survives.
        # (Skipped entirely in --incremental mode, which appends to them instead.)
        stale = [p for p in trustprime_data_paths(suffix) if os.path.exists(p)]
        if os.path.exists(args.output_tasks):
            stale.append(args.output_tasks)
        if stale:
            if not args.force:
                print("The following generated files will be removed and regenerated:")
                for p in stale:
                    print(f"  {p}")
                print("(the coherence cache is preserved)")
                if not sys.stdin.isatty():
                    sys.exit("Refusing to overwrite without --force in a non-interactive shell.")
                if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                    sys.exit("Aborted.")
            for p in stale:
                os.remove(p)

    tasks = load_source_tasks(args.source_tasks)
    titles = load_scenario_titles(args.source_tasks, len(tasks))
    persona_key, persona_desc = detect_persona(args.source_tasks)
    scenario_of = derive_scenarios(tasks)
    # Explicit "# SCENARIO N" labels (groups happy-path benigns under their real
    # scenario), used to keep only same-scenario benign counterparts in the output.
    scenario_label = derive_scenario_labels(args.source_tasks, len(tasks))
    print(
        f"Loaded {len(tasks)} tasks ({sum(1 for t in tasks if t.task_type == 'benign')} "
        f"benign, {sum(1 for t in tasks if t.task_type == 'adversarial')} adversarial) "
        f"from {args.source_tasks} [persona: {persona_key}]"
    )

    from tact_bench.envs.retail.data import load_data

    data = load_data()
    wiki_path = os.path.join(RETAIL_DIR, "wiki.md")
    with open(wiki_path) as f:
        wiki_text = f.read()
    # Truncate to <= ~4000 tokens (~4 chars/token).
    if len(wiki_text) > 16000:
        wiki_text = wiki_text[:16000] + "\n\n[... policy truncated for length ...]"

    tool_params = build_tool_param_map()
    tool_names = sorted(tool_params.keys())

    json_mode_state = {
        "enabled": (args.model_provider or "") in _JSON_MODE_PROVIDERS
        or args.model.startswith("gpt-")
    }

    # Incremental mode: keep the trust-primed tasks already in the output module
    # and skip their (benign, adversarial) pairs so only missing pairs are screened
    # and generated, then append.
    existing_accepted: List[Dict[str, Any]] = []
    exclude_pairs: frozenset = frozenset()
    existing_data: Dict[str, Dict[str, Any]] = {}
    if args.incremental:
        if os.path.exists(args.output_tasks):
            existing = load_existing_suite(args.output_tasks)
            existing_accepted = existing["accepted"]
            exclude_pairs = frozenset(existing["pairs"])
            print(
                f"Incremental: keeping {len(existing_accepted)} existing task(s); "
                f"skipping their {len(exclude_pairs)} pair(s)."
            )
        else:
            print(f"Incremental: {args.output_tasks} does not exist yet — building fresh.")
        # Load the augmentation data already in the suite so newly generated
        # entities/users are checked against it (no silent id/identity collisions).
        for table in AUGMENTABLE_TABLES:
            p = os.path.join(DATA_DIR, f"{table}_{suffix}.json")
            if os.path.exists(p):
                with open(p) as f:
                    existing_data[table] = json.load(f)

    suite = generate_suite(
        args,
        tasks=tasks,
        titles=titles,
        scenario_of=scenario_of,
        persona_key=persona_key,
        persona_desc=persona_desc,
        data=data,
        wiki_text=wiki_text,
        tool_names=tool_names,
        tool_params=tool_params,
        json_mode_state=json_mode_state,
        exclude_pairs=exclude_pairs,
    )

    new_kept, merged_aug, dropped_msgs = merge_augmentations(
        suite["generated"], existing=existing_data
    )
    for msg in dropped_msgs:
        print(f"WARNING: {msg}")
    # Existing tasks first (preserved verbatim), then the newly generated ones.
    accepted = existing_accepted + new_kept

    # source benign index -> output index, keeping ONLY the benign tasks the
    # generated trust-primed tasks actually reference (their adversarial halves'
    # benign counterparts), in source order. Unrelated benign scenarios are
    # dropped so a small suite stays small.
    benign_out_index = {
        src_idx: out_idx
        for out_idx, src_idx in enumerate(
            selected_benign_sources(accepted, tasks, scenario_label)
        )
    }

    module_text = render_tasks_module(
        source_path=args.source_tasks,
        tasks=tasks,
        titles=titles,
        benign_out_index=benign_out_index,
        accepted=accepted,
        args=args,
        scenario_label=scenario_label,
    )
    with open(args.output_tasks, "w") as f:
        f.write(module_text)

    written_data_files = []
    for table in AUGMENTABLE_TABLES:
        new_entries = merged_aug.get(table) or {}
        # In incremental mode overlay the new entities on the existing ones (already
        # loaded above); otherwise existing_data is empty and this is just the new set.
        entries = {**existing_data.get(table, {}), **new_entries}
        if not entries:
            continue
        path = os.path.join(DATA_DIR, f"{table}_{suffix}.json")
        with open(path, "w") as f:
            json.dump(entries, f, indent=2, sort_keys=True)
            f.write("\n")
        written_data_files.append(path)

    _print_summary(
        suite=suite,
        accepted=accepted,
        dropped_msgs=dropped_msgs,
        benign_out_index=benign_out_index,
        written_data_files=written_data_files,
        output_tasks=args.output_tasks,
        suffix=suffix,
    )


def _print_summary(
    *,
    suite: Dict[str, Any],
    accepted: List[Dict[str, Any]],
    dropped_msgs: List[str],
    benign_out_index: Dict[int, int],
    written_data_files: List[str],
    output_tasks: str,
    suffix: str,
) -> None:
    s = suite["stats"]
    print("\n" + "=" * 60)
    print("TRUST-PRIME GENERATION SUMMARY")
    print("=" * 60)
    print(f"Candidate pool (M):          {suite['pairs_total']}")
    print(f"Target min(M, N):            {suite['target']}")
    print(f"STEP 0 — coherence screened: {s['coherence_screened']} (2 LLM judges each)")
    print(f"STEP 0 — cache hits:         {s['cache_hits']} (no LLM call)")
    print(f"  judged coherent:           {s['accepted_coherent']}")
    print(f"  incoherent (both judges):  {len(s['rejected'])}")
    for (b, a), reason in s["rejected"]:
        print(f"      (benign={b}, adv={a}): {reason}")
    print(f"  judges disagreed:          {len(s['disagreed'])}")
    for (b, a), (ra, rb) in s["disagreed"]:
        print(f"      (benign={b}, adv={a}): auditor={ra!r} / analyst={rb!r}")
    print(f"  coherence errors (retry):  {len(s['coherence_error'])}")
    print(f"STEP 1 — generation rejected:{len(s['gen_rejected'])}")
    for (b, a), reason in s["gen_rejected"]:
        print(f"      (benign={b}, adv={a}): {reason}")
    print(f"STEP 1 — generation failed:  {len(s['gen_failed'])}")
    for (b, a), reason in s["gen_failed"]:
        print(f"      (benign={b}, adv={a}): {reason}")
    print(f"Dropped at merge:            {len(dropped_msgs)}")
    print(f"\nCoherence cache:             {suite['cache_path']}")
    print(f"Output tasks module:         {output_tasks}")
    print(
        f"  ({len(benign_out_index)} preserved benign + {len(accepted)} "
        "trust-primed adversarial)"
    )
    for path in written_data_files:
        print(f"Augmentation data:           {path}")
    print(f"\nRun with: python run.py --env retail --task-variant {suffix} ...")

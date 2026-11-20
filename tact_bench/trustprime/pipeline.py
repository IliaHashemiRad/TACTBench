"""Cache-aware two-stage generation loop.

Walks the seed-shuffled candidate pool one pair at a time (in concurrent batches)
and, for each pair:

  1. STEP 0 — consult the persistent coherence cache. On a miss, run the two-judge
     ensemble (coherence.py) and record the verdict (accepted/rejected/disagreed).
  2. STEP 1 — only ``accepted`` pairs are synthesized into a task (generate.py).

It stops as soon as ``min(M, N)`` tasks have been generated (M = size of the
rule-based candidate pool, N = ``--num-tasks``) or the pool is exhausted. Because
verdicts are cached in a file that --clean/--force never touch, a later run with a
different N re-uses them: a cached rejected/disagreed pair is skipped with no LLM
call, and a cached accepted pair goes straight to generation.

``check_pair_coherence`` and ``generate_for_pair`` are imported as module-level
names so they can be monkeypatched in offline tests.
"""

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from tact_bench.types import Task
from tact_bench.trustprime.pairing import build_candidate_pairs
from tact_bench.trustprime.coherence import check_pair_coherence
from tact_bench.trustprime.generate import generate_for_pair
from tact_bench.trustprime.cache import (
    cache_key,
    cache_path,
    content_hash,
    get_valid,
    load_with_migration,
    save_cache,
)

Pair = Tuple[int, int]


def _status_str(out: Dict[str, Any]) -> str:
    src = "cache" if out["from_cache"] else "screened"
    v = out["verdict"]
    if v == "accepted":
        gen = out["gen"] or {}
        status = gen.get("status")
        if status == "accepted":
            return f"coherent [{src}] → generated ✓"
        if status == "rejected":
            return f"coherent [{src}] → generation rejected: {gen.get('reason', '')}"
        return f"coherent [{src}] → generation FAILED: {gen.get('reason', 'no result')}"
    if v == "rejected":
        return f"incoherent [{src}] (both judges agree)"
    if v == "disagreed":
        return f"judges DISAGREED [{src}] — skipped (recorded)"
    return f"coherence ERROR [{src}] — skipped (will retry next run)"


def generate_suite(
    args,
    *,
    tasks: List[Task],
    titles: List[str],
    scenario_of: Dict[int, int],
    persona_key: str,
    persona_desc: str,
    data: Dict[str, Any],
    wiki_text: str,
    tool_names: List[str],
    tool_params: Dict[str, List[str]],
    json_mode_state: Dict[str, bool],
    exclude_pairs: frozenset = frozenset(),
) -> Dict[str, Any]:
    """Run the cache-aware two-stage loop. Returns generated tasks + stats.

    ``exclude_pairs`` (used by --incremental) drops pairs already present in the
    output suite from the candidate pool entirely, so they are neither re-screened
    (no STEP-0 LLM calls, nothing re-cached) nor regenerated.
    """
    pairs, filter_stats = build_candidate_pairs(tasks, scenario_of)
    M_all = len(pairs)
    if exclude_pairs:
        pairs = [p for p in pairs if p not in exclude_pairs]
    random.Random(args.seed).shuffle(pairs)
    M = len(pairs)
    target = min(M, args.num_tasks)
    excluded_note = (
        f" ({M_all - M} already-present pair(s) excluded)" if exclude_pairs else ""
    )
    print(
        f"Candidate pool: {M} valid cross-scenario pairings{excluded_note} "
        f"(filtered: {filter_stats})"
    )
    print(f"Target: generate min(M={M}, N={args.num_tasks}) = {target} task(s)")

    cpath = cache_path(args.source_tasks)
    cache = load_with_migration(args.source_tasks, tasks)
    if cache:
        print(f"Loaded {len(cache)} cached coherence verdict(s) from {cpath}")
    cache_lock = threading.Lock()

    stats: Dict[str, Any] = {
        "cache_hits": 0,         # pairs resolved from cache (no LLM)
        "coherence_screened": 0, # pairs that triggered the 2-judge ensemble this run
        "accepted_coherent": 0,  # pairs judged coherent (cache or fresh)
        "rejected": [],          # (pair, reason) — both judges said incoherent
        "disagreed": [],         # (pair, (reason_auditor, reason_analyst))
        "coherence_error": [],   # (pair, reason) — LLM/parse failure, not cached
        "gen_rejected": [],      # (pair, reason) — coherent but generator rejected
        "gen_failed": [],        # (pair, reason) — coherent but generation/validation failed
    }
    generated: List[Dict[str, Any]] = []

    def _worker(pair: Pair) -> Dict[str, Any]:
        h = content_hash(pair, tasks)
        with cache_lock:
            entry = get_valid(cache, pair, h)
        from_cache = entry is not None
        if entry is None:
            entry = check_pair_coherence(
                pair,
                tasks=tasks,
                titles=titles,
                args=args,
                json_mode_state=json_mode_state,
            )
            # Cache only settled verdicts; a transient "error" must be retried.
            if entry["verdict"] in ("accepted", "rejected", "disagreed"):
                with cache_lock:
                    cache[cache_key(pair)] = {
                        "verdict": entry["verdict"],
                        "note": entry.get("note", ""),
                        "reason_auditor": entry.get("reason_auditor", ""),
                        "reason_analyst": entry.get("reason_analyst", ""),
                        "hash": h,
                        "model": args.model,  # which model produced this verdict (informational)
                    }
        out: Dict[str, Any] = {
            "pair": pair,
            "verdict": entry["verdict"],
            "from_cache": from_cache,
            "entry": entry,
            "gen": None,
        }
        if entry["verdict"] == "accepted":
            out["gen"] = generate_for_pair(
                pair,
                tasks=tasks,
                titles=titles,
                scenario_of=scenario_of,
                persona_key=persona_key,
                persona_desc=persona_desc,
                data=data,
                wiki_text=wiki_text,
                tool_names=tool_names,
                tool_params=tool_params,
                args=args,
                json_mode_state=json_mode_state,
                coherence_note=entry.get("note", ""),
            )
        return out

    def _tally(out: Dict[str, Any]) -> None:
        pair, entry, v = out["pair"], out["entry"], out["verdict"]
        if out["from_cache"]:
            stats["cache_hits"] += 1
        else:
            stats["coherence_screened"] += 1
        if v == "accepted":
            stats["accepted_coherent"] += 1
            gen = out["gen"] or {}
            if gen.get("status") == "accepted":
                generated.append(gen["result"])
            elif gen.get("status") == "rejected":
                stats["gen_rejected"].append((pair, gen.get("reason", "")))
            else:
                stats["gen_failed"].append((pair, gen.get("reason", "no result")))
        elif v == "rejected":
            stats["rejected"].append(
                (pair, entry.get("reason_auditor") or entry.get("reason_analyst", ""))
            )
        elif v == "disagreed":
            stats["disagreed"].append(
                (pair, (entry.get("reason_auditor", ""), entry.get("reason_analyst", "")))
            )
        else:
            stats["coherence_error"].append((pair, entry.get("reason_auditor", "")))

    pair_iter = iter(pairs)
    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        while len(generated) < target:
            need = target - len(generated)
            batch: List[Pair] = []
            for _ in range(need):
                try:
                    batch.append(next(pair_iter))
                except StopIteration:
                    break
            if not batch:
                break
            for out in executor.map(_worker, batch):
                _tally(out)
                b, a = out["pair"]
                print(
                    f"  (benign={b}, adv={a}) → {_status_str(out)}  "
                    f"[{len(generated)}/{target} generated]"
                )
            # Persist verdicts after every batch so an interrupted run keeps them.
            with cache_lock:
                save_cache(cpath, cache)

    if len(generated) < target:
        print(
            f"WARNING: only {len(generated)}/{target} task(s) generated before the "
            "candidate pool was exhausted; writing what we have."
        )

    return {
        "generated": generated,
        "stats": stats,
        "cache_path": cpath,
        "pairs_total": M,
        "target": target,
        "filter_stats": filter_stats,
    }

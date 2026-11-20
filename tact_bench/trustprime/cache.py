"""Persistent STEP-0 coherence-verdict cache.

Each ``(benign_idx, adversarial_idx)`` pairing's story-coherence verdict is stored
so re-runs — including runs with a different ``--num-tasks`` OR a different model —
never re-screen a pair that has already been judged. On a later run a cached pair
skips STEP 0 entirely: a cached ``rejected``/``disagreed`` pair is passed over with
no LLM call, and a cached ``accepted`` pair goes straight to generation.

ONE cache file per source suite (``coherence__<source-stem>.json``), shared across
ALL models. Coherence is treated as an (approximately) objective property of the
pair — "do these two requests fit as one story" — judged by a two-prompt ensemble,
so a verdict produced by any model is reused by later runs of any model. The entry
records which model produced it (``model`` field) for transparency only; reuse does
not depend on it. (If you ever want a specific model to re-judge from scratch, use
``--clean-cache``.)

The cache lives in its own directory (``constants.COHERENCE_CACHE_DIR``), separate
from the generated suite, so it is NOT removed by ``--clean`` or the ``--force``
regeneration sweep.

Each entry carries a content hash of the two source instructions + their forbidden
actions + the prompt version. If any of those change, the stale entry is ignored
(auto-invalidation) and the pair is re-screened. The model is deliberately NOT part
of the hash.
"""

import glob
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from tact_bench.types import Task
from tact_bench.trustprime.constants import COHERENCE_CACHE_DIR

# Bump this whenever the coherence prompts (coherence.py) change in a way that
# could alter verdicts, so previously cached entries are recomputed rather than
# trusted under the new prompts.
#   v1 -> v2: judges now ignore placeholder names/ids/account data and judge only
#             the story/semantics (the two halves are merged onto one new user, so
#             identity mismatches between them are expected, not incoherent).
#   v2 -> v3: "contradiction" sharpened to genuine logical impossibility (one
#             premise negates a precondition the other needs in the same causal
#             chain); "different/partial access" or unrelated coexisting account
#             facts are no longer treated as contradictions.
#   v3 -> v4: rule 2 strengthened — the benign OUTCOME must not moot/contradict the
#             adversarial pivot's stated need/motivation (not just its account-state
#             precondition), judged on the benign tool calls' result so reframing
#             can't dodge it.
COHERENCE_PROMPT_VERSION = "4"

Pair = Tuple[int, int]


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "x"


def _stem(source_path: str) -> str:
    return _sanitize(os.path.splitext(os.path.basename(source_path))[0])


def cache_path(source_path: str) -> str:
    """The single, model-agnostic cache file for a source suite.

    Pair indices are only meaningful within a given source suite, so the file is
    keyed by the source stem alone — every model shares it.
    """
    return os.path.join(COHERENCE_CACHE_DIR, f"coherence__{_stem(source_path)}.json")


def cache_key(pair: Pair) -> str:
    return f"{pair[0]},{pair[1]}"


def _hash_payload(parts: List[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def content_hash(pair: Pair, tasks: List[Task]) -> str:
    """Model-agnostic fingerprint of everything a verdict depends on."""
    b, a = pair
    return _hash_payload(
        [
            COHERENCE_PROMPT_VERSION,
            tasks[b].instruction,
            tasks[a].instruction,
            repr(sorted(tasks[b].forbidden_actions or [])),
            repr(sorted(tasks[a].forbidden_actions or [])),
        ]
    )


def _legacy_content_hash(pair: Pair, tasks: List[Task], model: str) -> str:
    """Reproduces the OLD per-model hash (model included). Used only to verify a
    legacy entry is still valid for the current source before migrating it."""
    b, a = pair
    return _hash_payload(
        [
            COHERENCE_PROMPT_VERSION,
            model or "",
            tasks[b].instruction,
            tasks[a].instruction,
            repr(sorted(tasks[b].forbidden_actions or [])),
            repr(sorted(tasks[a].forbidden_actions or [])),
        ]
    )


def load_cache(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # A corrupt cache must never abort generation; start fresh.
        return {}


def save_cache(path: str, cache: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)  # atomic; concurrent writers never see a half file


def get_valid(
    cache: Dict[str, Any], pair: Pair, expected_hash: str
) -> Optional[Dict[str, Any]]:
    """Return the cached entry for ``pair`` only if its content hash still matches."""
    entry = cache.get(cache_key(pair))
    if isinstance(entry, dict) and entry.get("hash") == expected_hash:
        return entry
    return None


def load_with_migration(source_path: str, tasks: List[Task]) -> Dict[str, Any]:
    """Load the shared cache, folding in any legacy per-model files.

    Earlier versions wrote one file per (source, model): ``coherence__<stem>__<model>
    .json``. This merges every such file for this source into the single shared
    ``coherence__<stem>.json`` (re-hashing each entry to the model-agnostic scheme),
    then removes the now-redundant per-model files so exactly one cache file remains.
    A legacy entry is only carried over if its stored hash still matches the current
    source (under the old model-aware scheme) — otherwise it is stale and dropped so
    the pair is re-screened. Idempotent: once consolidated, there are no legacy files
    left to import.
    """
    new_path = cache_path(source_path)
    merged = load_cache(new_path)

    stem = _stem(source_path)
    prefix = f"coherence__{stem}__"
    legacy_paths = sorted(
        p
        for p in glob.glob(os.path.join(COHERENCE_CACHE_DIR, f"{prefix}*.json"))
        if os.path.basename(p) != f"coherence__{stem}.json"
    )
    if not legacy_paths:
        return merged

    imported = 0
    for lp in legacy_paths:
        model = os.path.basename(lp)[len(prefix):-len(".json")]
        for key, entry in load_cache(lp).items():
            if key in merged or not isinstance(entry, dict):
                continue
            try:
                b, a = (int(x) for x in key.split(","))
            except ValueError:
                continue
            if b >= len(tasks) or a >= len(tasks):
                continue
            # Only migrate entries still valid for the current source text.
            if entry.get("hash") != _legacy_content_hash((b, a), tasks, model):
                continue
            new_entry = dict(entry)
            new_entry["hash"] = content_hash((b, a), tasks)
            new_entry.setdefault("model", model)
            merged[key] = new_entry
            imported += 1

    save_cache(new_path, merged)
    for lp in legacy_paths:
        try:
            os.remove(lp)
        except OSError:
            pass
    print(
        f"Consolidated {len(legacy_paths)} legacy per-model cache file(s) into "
        f"{new_path} (imported {imported} verdict(s); {len(merged)} total)."
    )
    return merged


def purge() -> List[str]:
    """Delete every cache file (used by --clean-cache). Returns removed paths."""
    removed: List[str] = []
    if os.path.isdir(COHERENCE_CACHE_DIR):
        for name in sorted(os.listdir(COHERENCE_CACHE_DIR)):
            p = os.path.join(COHERENCE_CACHE_DIR, name)
            if os.path.isfile(p):
                os.remove(p)
                removed.append(p)
    return removed

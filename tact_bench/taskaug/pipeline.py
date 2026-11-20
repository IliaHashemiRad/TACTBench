"""Orchestration: emit the originals (optional), then N augmented copies.

Each augmented copy is a full parallel remap of all source tasks: every acting
customer is replaced by a fresh twin, and every owned entity (order, trade-in,
pharmacy purchase, and any receipt named in a ground-truth action) is CLONED under
a fresh id owned by that twin. Because ids are freshly minted and the base data is
never modified, many copies coexist in one merged dataset alongside the untouched
originals — so a single combined suite can hold the 40 originals plus 8x40 twins.

Same tools / forbidden_actions / task_type / story throughout; only identity DATA
and the entity ids that thread through it differ. ``benign_counterpart_indices``
are offset per block so a copy's adversarial task points at that same copy's
benign counterpart (the twin who can actually complete it).

Entity ownership:
  * orders / trade_ins / pharmacy_purchases carry a ``user_id`` back-reference.
  * receipts have no ``user_id``; a receipt is owned by whichever task names it in
    a GROUND-TRUTH action. Receipts named only in an adversarial instruction (the
    "someone else's receipt" decoys) are never cloned, so the attack stays real.
"""

import copy
from typing import Any, Dict, List, Optional

from tact_bench.types import Task
from tact_bench.taskaug.identity import IdentityAllocator, build_twin
from tact_bench.taskaug.remap import (
    apply_token_map,
    combine_maps,
    email_leak_fragments,
    hard_tokens,
    referenced_user_ids,
    remap_kwargs,
    remap_value,
    verify_no_leak,
)

# entity table -> the base data table it clones from.
OWNED_TABLES = {
    "orders": "orders",
    "trade_ins": "trade_ins",
    "pharmacy": "pharmacy_purchases",
    "receipts": "receipts",
}


def _collect_referenced_users(tasks: List[Task], base_users: Dict[str, Any]) -> List[str]:
    referenced: set = set()
    for t in tasks:
        referenced.add(t.user_id)
        referenced.update(referenced_user_ids(t.instruction, [a.kwargs for a in t.actions], base_users))
    return sorted(u for u in referenced if u in base_users)


def _household_groups(referenced: List[str], base_users: Dict[str, Any]) -> List[List[str]]:
    """Base users sharing an address (a household, e.g. the spouses in the
    pharmacy-privacy scenario). Instructions lean on that shared state ("we live
    at the same address"), so each group's twins must keep sharing a new address
    — and, when the members also share a surname, a new surname."""
    by_addr: Dict[Any, List[str]] = {}
    for u in referenced:
        addr = base_users[u].get("address") or {}
        if addr.get("address1"):
            by_addr.setdefault((addr["address1"], addr.get("zip")), []).append(u)
    return [sorted(g) for g in by_addr.values() if len(g) > 1]


def _receipt_owners(tasks: List[Task]) -> Dict[str, str]:
    """Map receipt_id -> owning (old) user_id, from ground-truth actions only."""
    owners: Dict[str, str] = {}
    for t in tasks:
        for a in t.actions:
            rid = a.kwargs.get("receipt_id")
            if isinstance(rid, str):
                owners.setdefault(rid, t.user_id)
    return owners


def _owned_entity_ids(
    tasks: List[Task], data: Dict[str, Any], referenced: List[str]
) -> Dict[str, Dict[str, List[str]]]:
    """For each referenced user, the ids of every entity it owns."""
    owned = {u: {"orders": set(), "trade_ins": set(), "receipts": set(), "pharmacy": set()}
             for u in referenced}
    for u in referenced:
        rec = data["users"].get(u, {})
        owned[u]["orders"].update(rec.get("orders", []) or [])
        owned[u]["trade_ins"].update(rec.get("trade_ins", []) or [])
    for oid, o in data.get("orders", {}).items():
        if o.get("user_id") in owned:
            owned[o["user_id"]]["orders"].add(oid)
    for tid, t in data.get("trade_ins", {}).items():
        if t.get("user_id") in owned:
            owned[t["user_id"]]["trade_ins"].add(tid)
    for pid, p in data.get("pharmacy_purchases", {}).items():
        if p.get("user_id") in owned:
            owned[p["user_id"]]["pharmacy"].add(pid)
    for rid, u in _receipt_owners(tasks).items():
        if u in owned and rid in data.get("receipts", {}):
            owned[u]["receipts"].add(rid)
    # drop ids with no clonable source record (keeps the id-remap but no ghost clone)
    for u in referenced:
        owned[u]["orders"] &= set(data.get("orders", {}))
        owned[u]["trade_ins"] &= set(data.get("trade_ins", {}))
        owned[u]["pharmacy"] &= set(data.get("pharmacy_purchases", {}))
    return {u: {k: sorted(v) for k, v in tbl.items()} for u, tbl in owned.items()}


def _orig_task(t: Task, idx: int) -> Dict[str, Any]:
    return {
        "user_id": t.user_id,
        "task_type": t.task_type,
        "forbidden_actions": t.forbidden_actions or [],
        "benign_counterpart_indices": t.benign_counterpart_indices,
        "instruction": t.instruction,
        "actions": [{"name": a.name, "kwargs": a.kwargs} for a in t.actions],
        "outputs": list(t.outputs or []),
        "source_index": idx,
        "source_user_id": t.user_id,
        "copy": None,
    }


def build_augmented_suite(
    tasks: List[Task],
    data: Dict[str, Any],
    seed: int = 0,
    copies: int = 1,
    with_original: bool = False,
) -> Dict[str, Any]:
    """Return the combined tasks + additive per-table data + a run report."""
    base_users = data["users"]
    alloc = IdentityAllocator(data, seed=seed)
    referenced = _collect_referenced_users(tasks, base_users)
    owned = _owned_entity_ids(tasks, data, referenced)
    households = _household_groups(referenced, base_users)

    n = len(tasks)
    aug_tasks: List[Dict[str, Any]] = []
    overrides: Dict[str, Dict[str, Any]] = {"users": {}, "orders": {}, "trade_ins": {},
                                            "receipts": {}, "pharmacy_purchases": {}}
    leaks: List[str] = []
    map_conflicts: List[str] = []

    if with_original:
        for idx, t in enumerate(tasks):
            aug_tasks.append(_orig_task(t, idx))

    for k in range(copies):
        # 0) per-copy shared identity for each household: one new address for
        # the group, and one new surname where the members share a base surname.
        pinned: Dict[str, Dict[str, Any]] = {}
        for group in households:
            street, zip_ = alloc.next_street(), alloc.new_zip()
            surnames = {base_users[u]["name"]["last_name"] for u in group}
            shared_surname = alloc.next_surname() if len(surnames) == 1 else None
            for u in group:
                pinned[u] = {"street": street, "zip_": zip_, "surname": shared_surname}
        # 1) twin every referenced user for this copy + clone its owned entities.
        twin_map: Dict[str, Dict[str, str]] = {}
        twin_uid: Dict[str, str] = {}
        for u in referenced:
            new_uid, twin_rec, tmap = build_twin(
                u, base_users[u], alloc, owned_ids=owned[u], **pinned.get(u, {})
            )
            twin_map[u] = tmap
            twin_uid[u] = new_uid
            overrides["users"][new_uid] = twin_rec
            for key, table in OWNED_TABLES.items():
                for old_id in owned[u][key]:
                    if old_id in data.get(table, {}):
                        overrides[table][tmap[old_id]] = remap_value(
                            copy.deepcopy(data[table][old_id]), tmap
                        )
        # 2) remap every task against this copy's twins.
        block_offset = (n if with_original else 0) + k * n
        for idx, t in enumerate(tasks):
            uids = sorted(set([t.user_id]) | set(
                referenced_user_ids(t.instruction, [a.kwargs for a in t.actions], base_users)))
            uids = [u for u in uids if u in twin_map]
            conflicts: List[Any] = []
            cmap = combine_maps((twin_map[u] for u in uids), conflicts=conflicts)
            for key, kept, winner in conflicts:
                map_conflicts.append(
                    f"copy {k+1} task {idx}: users {uids} disagree on {key!r} "
                    f"({kept!r} vs {winner!r}; later map wins)"
                )
            new_instruction = apply_token_map(t.instruction, cmap)
            counterparts = (
                [block_offset + c for c in t.benign_counterpart_indices]
                if t.benign_counterpart_indices else t.benign_counterpart_indices
            )
            aug_tasks.append({
                "user_id": twin_uid[t.user_id],
                "task_type": t.task_type,
                "forbidden_actions": t.forbidden_actions or [],
                "benign_counterpart_indices": counterparts,
                "instruction": new_instruction,
                "actions": [{"name": a.name, "kwargs": remap_kwargs(a.kwargs, cmap)} for a in t.actions],
                "outputs": [apply_token_map(o, cmap) for o in (t.outputs or [])],
                "source_index": idx,
                "source_user_id": t.user_id,
                "copy": k + 1,
            })
            residual = verify_no_leak(
                new_instruction,
                hard_tokens(cmap, base_users, uids),
                email_fragments=email_leak_fragments(cmap, base_users, uids),
            )
            if residual:
                leaks.append(f"copy {k+1} task {idx} (user {t.user_id}): residual old tokens {residual}")

    return {
        "tasks": aug_tasks,
        "overrides": overrides,
        "twin_count": len(overrides["users"]),
        "referenced": referenced,
        "households": households,
        "copies": copies,
        "with_original": with_original,
        "leaks": leaks,
        "map_conflicts": map_conflicts,
    }

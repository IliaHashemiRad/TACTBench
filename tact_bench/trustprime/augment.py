"""Merging per-task augmentation entities and managing generated files.

``merge_augmentations`` combines the accepted tasks' new entities into one set
per table, dropping any task whose entity id collides with an earlier task's
under a different definition. ``clean_all`` removes every generated trust-prime
artifact (data files and tasks modules).
"""

import glob
import os
from typing import Any, Dict, List, Optional, Tuple

from tact_bench.trustprime.constants import AUGMENTABLE_TABLES, DATA_DIR, RETAIL_DIR


def _identity_collision(
    item: Dict[str, Any],
    seen_user_ids: set,
    seen_usernames: set,
    seen_emails: set,
    seen_name_zip: set,
) -> str:
    """Return a description if this item's new user collides with an earlier kept
    user on any identity-lookup field, else ''.

    The find_* tools (find_user_id_by_name_zip / find_user_by_username /
    find_user_id_by_email) return the FIRST record matching an exact field, so two
    generated users sharing a user_id, username, email, or (first, last, zip) would
    let the agent fetch the wrong account. This is the final guarantee on top of the
    per-pair-unique ids (ids.py) and the validation checks (validation.py).
    """
    for uid, rec in (item["data_augmentation"].get("users") or {}).items():
        name = rec.get("name") or {}
        first = str(name.get("first_name", "")).lower()
        last = str(name.get("last_name", "")).lower()
        zip_ = str((rec.get("address") or {}).get("zip", ""))
        username = str(rec.get("username", "")).lower()
        email = str(rec.get("email", "")).lower()
        if uid in seen_user_ids:
            return f"user_id {uid!r}"
        if username and username in seen_usernames:
            return f"username {username!r}"
        if email and email in seen_emails:
            return f"email {email!r}"
        if (first, last, zip_) in seen_name_zip:
            return f"(name, zip) ({first!r}, {last!r}, {zip_!r})"
    return ""


def _record_user(
    uid: str,
    rec: Dict[str, Any],
    seen_user_ids: set,
    seen_usernames: set,
    seen_emails: set,
    seen_name_zip: set,
) -> None:
    name = rec.get("name") or {}
    seen_user_ids.add(uid)
    if rec.get("username"):
        seen_usernames.add(str(rec["username"]).lower())
    if rec.get("email"):
        seen_emails.add(str(rec["email"]).lower())
    seen_name_zip.add(
        (
            str(name.get("first_name", "")).lower(),
            str(name.get("last_name", "")).lower(),
            str((rec.get("address") or {}).get("zip", "")),
        )
    )


def _record_identity(
    item: Dict[str, Any],
    seen_user_ids: set,
    seen_usernames: set,
    seen_emails: set,
    seen_name_zip: set,
) -> None:
    for uid, rec in (item["data_augmentation"].get("users") or {}).items():
        _record_user(uid, rec, seen_user_ids, seen_usernames, seen_emails, seen_name_zip)


def merge_augmentations(
    accepted: List[Dict[str, Any]],
    existing: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """Merge per-task augmentations, deduplicating identical entities.

    Tasks are dropped (returned in `dropped_msgs`) if their entities collide with
    an earlier task's under the same id but a different definition, OR if their new
    user collides with an earlier kept user on any identity-lookup field (user_id,
    username, email, or (first, last, zip)).

    ``existing`` is the augmentation data already present in the suite (used by
    --incremental): a {table: {id: record}} map. New tasks are checked against it
    too, so a newly generated entity/user can never silently overwrite or collide
    with one already in the suite. ``merged`` still returns ONLY the new entities;
    the caller overlays them on ``existing`` when writing.
    """
    existing = existing or {}
    merged: Dict[str, Dict[str, Any]] = {}
    kept: List[Dict[str, Any]] = []
    dropped_msgs: List[str] = []
    seen_user_ids: set = set()
    seen_usernames: set = set()
    seen_emails: set = set()
    seen_name_zip: set = set()
    # Seed the identity guards with users already in the suite so a new user cannot
    # collide with an existing one on any find_* lookup field.
    for uid, rec in (existing.get("users") or {}).items():
        _record_user(uid, rec, seen_user_ids, seen_usernames, seen_emails, seen_name_zip)
    for item in accepted:
        conflict = None
        for table, entries in item["data_augmentation"].items():
            existing_table = existing.get(table) or {}
            for key, record in entries.items():
                # Collide with an earlier NEW entity or one already in the suite.
                prior = merged.get(table, {}).get(key)
                if prior is None:
                    prior = existing_table.get(key)
                if prior is not None and prior != record:
                    conflict = f"{table}/{key}"
                    break
            if conflict:
                break
        if conflict:
            dropped_msgs.append(
                f"pair (benign={item['source_benign_index']}, "
                f"adv={item['source_adversarial_index']}) dropped: entity id "
                f"collision on {conflict} with a different definition"
            )
            continue
        id_collision = _identity_collision(
            item, seen_user_ids, seen_usernames, seen_emails, seen_name_zip
        )
        if id_collision:
            dropped_msgs.append(
                f"pair (benign={item['source_benign_index']}, "
                f"adv={item['source_adversarial_index']}) dropped: new user collides "
                f"with an earlier task on {id_collision} (find_* tools would fetch the "
                "wrong account)"
            )
            continue
        for table, entries in item["data_augmentation"].items():
            merged.setdefault(table, {}).update(entries)
        _record_identity(item, seen_user_ids, seen_usernames, seen_emails, seen_name_zip)
        kept.append(item)
    return kept, merged, dropped_msgs


def trustprime_data_paths(suffix: str) -> List[str]:
    return [
        os.path.join(DATA_DIR, f"{table}_{suffix}.json") for table in AUGMENTABLE_TABLES
    ]


def clean_all() -> None:
    removed = []
    for path in glob.glob(os.path.join(DATA_DIR, "*trustprime*.json")):
        os.remove(path)
        removed.append(path)
    for path in glob.glob(os.path.join(RETAIL_DIR, "tasks_*trustprime*.py")):
        os.remove(path)
        removed.append(path)
    for path in glob.glob(
        os.path.join(RETAIL_DIR, "__pycache__", "tasks_*trustprime*.pyc")
    ):
        os.remove(path)
        removed.append(path)
    if removed:
        print("Removed:")
        for path in removed:
            print(f"  {path}")
    else:
        print("Nothing to clean.")

"""Rule-based (no-LLM) rewriting of every place an old identity appears.

One engine, ``apply_token_map``, rewrites free text (the instruction), structured
action kwargs, and owned-entity records alike. It is deliberately conservative:

  * Replacements are applied LONGEST-key-first, so a full email/name is consumed
    before its bare fragments (username, first name) are considered.
  * A key made only of word characters (names, zips, card last-fours, user ids,
    payment-method ids) is matched with ``\\b`` word boundaries, so it can never
    fire inside a longer token — a 4-digit card last-four cannot rewrite a digit
    inside a 10-digit item id or a price, and a first name cannot rewrite a
    substring of an unrelated word.
  * Keys containing punctuation (emails, phones) are matched literally.

Because JSON numbers (prices, amounts, points) are never strings, applying the map
to an entity's string leaves only ever touches genuine identity fields (user_id,
pickup_person name, payment_method_id, card last-four, email, phone).
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _rewrite_email_token(token: str, keys_desc: List[str], token_map: Dict[str, str]) -> str:
    """Substring (no-boundary) replacement inside one email-shaped token.

    Look-alike/decoy addresses fuse identity fragments with digits
    ("emily.carter3482@..."), where word-boundary replacement cannot reach the
    glued surname. Replacing map keys as raw substrings inside the token keeps a
    decoy's relationship to the real address (e.g. one character off) intact:
    "emily.carter3482@..." -> "bruno.larsson3482@..." when the real email
    "emily.carter3481@..." maps to "bruno.larsson3481@...".

    All keys are applied in ONE regex pass (longest-first alternation) so
    replaced text is never rescanned — sequential str.replace would re-match a
    key inside an inserted value (e.g. "lena" inside a new name "helena").
    """
    if not keys_desc:
        return token
    pattern = re.compile("|".join(re.escape(k) for k in keys_desc))
    return pattern.sub(lambda m: token_map[m.group(0)], token)


def apply_token_map(text: str, token_map: Dict[str, str]) -> str:
    """Rewrite ``text`` by replacing every old identity token with its new value."""
    if not text or not token_map:
        return text
    keys_desc = [
        old for old in sorted(token_map, key=len, reverse=True)
        if old and old != token_map[old]
    ]
    # Pass 1: email-shaped tokens get substring replacement (see helper above).
    text = EMAIL_RE.sub(lambda m: _rewrite_email_token(m.group(0), keys_desc, token_map), text)
    # Pass 2: everything else, longest-first with word boundaries for word-only keys.
    for old in keys_desc:
        new = token_map[old]
        if re.fullmatch(r"\w+", old):
            text = re.sub(rf"\b{re.escape(old)}\b", lambda m, n=new: n, text)
        else:
            text = text.replace(old, new)
    return text


def remap_value(val: Any, token_map: Dict[str, str]) -> Any:
    """Recursively rewrite the string leaves of a kwargs/entity value."""
    if isinstance(val, str):
        return apply_token_map(val, token_map)
    if isinstance(val, list):
        return [remap_value(v, token_map) for v in val]
    if isinstance(val, dict):
        return {k: remap_value(v, token_map) for k, v in val.items()}
    return val


def remap_kwargs(kwargs: Dict[str, Any], token_map: Dict[str, str]) -> Dict[str, Any]:
    """Rewrite identity values inside an action's kwargs (keys are untouched)."""
    return {k: remap_value(v, token_map) for k, v in kwargs.items()}


def combine_maps(
    maps: Iterable[Dict[str, str]],
    conflicts: Optional[List[Tuple[str, str, str]]] = None,
) -> Dict[str, str]:
    """Union of several per-user token maps (used for cross-user references).

    If two users' maps disagree on a key (e.g. an accidentally shared value that
    was not household-grouped), the later map wins and the clash is appended to
    ``conflicts`` as (key, kept_value_overwritten, winning_value) so the pipeline
    can surface it instead of silently rewriting with an arbitrary choice.
    """
    combined: Dict[str, str] = {}
    for m in maps:
        for key, val in m.items():
            if conflicts is not None and key in combined and combined[key] != val:
                conflicts.append((key, combined[key], val))
            combined[key] = val
    return combined


def referenced_user_ids(
    instruction: str, action_kwargs: List[Dict[str, Any]], base_users: Dict[str, Any]
) -> List[str]:
    """Base users a task actually mentions (so their maps are applied too).

    A task's own user is always included; another user is included when its
    user_id or full name appears in the instruction, or its user_id appears in
    any action kwarg — this is what keeps cross-account scenarios (e.g. a spouse's
    ``user_id`` named in an adversarial task) consistent after augmentation.
    """
    text = instruction or ""
    kwarg_blob = " ".join(
        str(v) for kw in action_kwargs for v in _flatten_strings(kw)
    )
    found: List[str] = []
    for uid, u in base_users.items():
        nm = u.get("name", {})
        full = f"{nm.get('first_name', '')} {nm.get('last_name', '')}".strip()
        if (
            uid in text
            or uid in kwarg_blob
            or (full and re.search(rf"\b{re.escape(full)}\b", text, flags=re.IGNORECASE))
        ):
            found.append(uid)
    return found


def _flatten_strings(val: Any) -> List[str]:
    if isinstance(val, str):
        return [val]
    if isinstance(val, dict):
        return [s for v in val.values() for s in _flatten_strings(v)]
    if isinstance(val, list):
        return [s for v in val for s in _flatten_strings(v)]
    return []


# Identity fields whose OLD value must not survive in the rewritten instruction.
# (Bare first/last names are excluded: they may legitimately appear as fragments
# of a deliberate look-alike decoy string, which is intentionally left alone.)
def hard_tokens(token_map: Dict[str, str], base_users: Dict[str, Any], uids: List[str]) -> List[str]:
    tokens: List[str] = []
    for uid in uids:
        u = base_users.get(uid, {})
        nm = u.get("name", {})
        full = f"{nm.get('first_name', '')} {nm.get('last_name', '')}".strip()
        for t in (uid, full, u.get("username"), u.get("email"),
                  u.get("recovery_email"), u.get("phone"),
                  str(u.get("address", {}).get("zip", ""))):
            if t and t in token_map:
                tokens.append(t)
        for pm in (u.get("payment_methods") or {}).values():
            lf = str(pm.get("last_four", ""))
            if lf and lf in token_map:
                tokens.append(lf)
    return tokens


def email_leak_fragments(
    token_map: Dict[str, str], base_users: Dict[str, Any], uids: List[str]
) -> List[str]:
    """Old name fragments that must not survive INSIDE email-shaped tokens.

    Word-boundary checks cannot see a surname fused into a decoy address
    ("...carter3482@..."), so these fragments get a substring check limited to
    emails. Fragments shorter than 4 chars, or ones that occur inside any NEW
    mapped value (e.g. old "lena" inside new first name "Helena"), are skipped
    to avoid false positives.
    """
    new_blob = " ".join(str(v).lower() for v in token_map.values())
    frags: List[str] = []
    for uid in uids:
        u = base_users.get(uid, {})
        nm = u.get("name", {})
        candidates = [
            str(nm.get("first_name", "")).lower(),
            str(nm.get("last_name", "")).lower(),
        ]
        for e in (u.get("email"), u.get("recovery_email")):
            if e:
                candidates.append(str(e).split("@")[0].lower())
        for c in candidates:
            if len(c) >= 4 and c not in new_blob:
                frags.append(c)
    return frags


def verify_no_leak(
    text: str, tokens: List[str], email_fragments: Iterable[str] = ()
) -> List[str]:
    """Return any hard identity token that still appears verbatim in ``text``,
    plus any old name fragment surviving inside an email-shaped token."""
    leaked = []
    for t in tokens:
        if re.fullmatch(r"\w+", t):
            if re.search(rf"\b{re.escape(t)}\b", text):
                leaked.append(t)
        elif t in text:
            leaked.append(t)
    fragments = [f for f in email_fragments if f]
    if fragments:
        for email in EMAIL_RE.findall(text):
            low = email.lower()
            for f in fragments:
                if f in low:
                    leaked.append(f"{f} (inside email {email})")
    return leaked

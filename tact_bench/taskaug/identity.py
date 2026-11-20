"""Minting a twin user record and the old->new token map for one base user.

``build_twin`` takes a base user record plus the ids of the entities that user
owns, and returns

    (twin_user_id, twin_record, token_map)

where ``twin_record`` has the SAME attribute schema as the base (same keys, same
authorization lists, same owned-entity arrays) but freshly-valued identity fields,
and ``token_map`` maps every old identity string — including the owned entity ids
(orders, trade-ins, receipts, pharmacy purchases) — to its new value. The map is
the single source of truth used later to rewrite the task instruction, the action
kwargs, and the cloned entity records, so the data and the text can never drift.

Entity ids are freshly minted (not reused) so many twins — several augmentation
copies of the same base user — can coexist in one merged dataset alongside the
untouched originals without any key collision.

Only identity/PII and owned-entity ids change. City, state, country, address2,
loyalty account, promotions, item/product ids, amounts, and every free-text /
business field are copied verbatim so the scenario is preserved.
"""

import copy
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from tact_bench.taskaug.names import (
    BASE_NAME_GENDER,
    FEMALE_FIRST_NAMES,
    LAST_NAMES,
    MALE_FIRST_NAMES,
    STREET_NAMES,
)
from tact_bench.taskaug.remap import remap_value


class IdentityAllocator:
    """Deterministic, collision-free source of twin identity + entity-id values.

    Seeded once per run; every ``used_*`` set is pre-loaded with the base
    dataset's values so a minted twin never collides with a real user (which
    would make a ``find_*`` lookup return the wrong account) nor with an earlier
    twin, and every fresh entity id is unique across the base and all copies.
    """

    def __init__(self, data: Dict[str, Any], seed: int = 0) -> None:
        base_users = data["users"]
        self.rng = random.Random(seed)
        # Gender-separated first-name cycles plus a surname stream. Twins take a
        # first name from the SAME gender pool as the base user so gendered
        # pronouns in the instructions stay coherent; (first, last) pairs are
        # kept globally unique via used_names.
        firsts_f = list(FEMALE_FIRST_NAMES)
        firsts_m = list(MALE_FIRST_NAMES)
        lasts = list(LAST_NAMES)
        self.rng.shuffle(firsts_f)
        self.rng.shuffle(firsts_m)
        self.rng.shuffle(lasts)
        self._firsts: Dict[str, List[str]] = {"F": firsts_f, "M": firsts_m}
        self._first_idx: Dict[str, int] = {"F": 0, "M": 0}
        self._lasts = lasts
        self._last_idx = 0
        streets = list(STREET_NAMES)
        self.rng.shuffle(streets)
        self._streets = streets
        self._street_idx = 0

        self.used_user_ids = set(base_users.keys())
        self.used_usernames = set()
        self.used_emails = set()
        self.used_names = set()  # (first_lower, last_lower)
        self.used_last_fours = set()
        self.used_card_ids = set()
        self.used_zips = set()
        self.used_phones = set()
        self.used_order_ids = set(data.get("orders", {}).keys())
        self.used_trade_in_ids = set(data.get("trade_ins", {}).keys())
        self.used_receipt_ids = set(data.get("receipts", {}).keys())
        self.used_pharmacy_ids = set(data.get("pharmacy_purchases", {}).keys())
        for u in base_users.values():
            nm = u.get("name", {})
            self.used_names.add(
                (str(nm.get("first_name", "")).lower(), str(nm.get("last_name", "")).lower())
            )
            if u.get("username"):
                self.used_usernames.add(str(u["username"]).lower())
            if u.get("email"):
                self.used_emails.add(str(u["email"]).lower())
            if u.get("recovery_email"):
                self.used_emails.add(str(u["recovery_email"]).lower())
            if u.get("address", {}).get("zip"):
                self.used_zips.add(str(u["address"]["zip"]))
            if u.get("phone"):
                self.used_phones.add(str(u["phone"]))
            if u.get("recovery_phone"):
                self.used_phones.add(str(u["recovery_phone"]))
            for pid, pm in (u.get("payment_methods") or {}).items():
                self.used_card_ids.add(pid)
                if pm.get("last_four"):
                    self.used_last_fours.add(str(pm["last_four"]))

    def next_surname(self) -> str:
        surname = self._lasts[self._last_idx % len(self._lasts)]
        self._last_idx += 1
        return surname

    def next_name(self, gender: str, last: Optional[str] = None) -> Tuple[str, str]:
        """A fresh (first, last) pair with the first name from the given gender
        pool. Pass ``last`` to pin the surname (household members share one)."""
        fixed_last = last is not None
        if last is None:
            last = self.next_surname()
        pool = self._firsts.get(gender) or self._firsts["F"]
        for _ in range(len(pool) * len(self._lasts)):
            n = len(pool)
            for off in range(n):
                first = pool[(self._first_idx[gender] + off) % n]
                if (first.lower(), last.lower()) not in self.used_names:
                    self._first_idx[gender] = (self._first_idx[gender] + off + 1) % n
                    self.used_names.add((first.lower(), last.lower()))
                    return first, last
            if fixed_last:
                break
            last = self.next_surname()
        raise RuntimeError("name pool exhausted; enlarge names.py pools")

    def next_street(self) -> str:
        street = self._streets[self._street_idx % len(self._streets)]
        self._street_idx += 1
        num = self.rng.randint(100, 9999)
        return f"{num} {street}"

    def _unique(self, gen, used: set) -> str:
        while True:
            v = gen()
            if v not in used:
                used.add(v)
                return v

    def new_zip(self, old_zip: str = "") -> str:
        return self._unique(lambda: f"{self.rng.randint(10000, 99999)}", self.used_zips)

    def new_last_four(self, old_lf: str = "") -> str:
        return self._unique(lambda: f"{self.rng.randint(0, 9999):04d}", self.used_last_fours)

    def new_digits(self, n: int) -> str:
        lo, hi = 10 ** (n - 1), 10 ** n - 1
        return f"{self.rng.randint(lo, hi)}"

    def new_phone(self, old_phone: str) -> str:
        """New phone with the area code/prefix preserved and a fresh final digit
        run, unique across the base dataset and all minted twins (the base data
        itself contains one duplicated phone; twins never add more)."""
        while True:
            candidate = _replace_last_digit_run(old_phone, self.new_digits(4))
            if candidate != old_phone and candidate not in self.used_phones:
                self.used_phones.add(candidate)
                return candidate

    def new_order_id(self) -> str:
        return self._unique(lambda: "#W" + self.new_digits(7), self.used_order_ids)

    def new_trade_in_id(self) -> str:
        return self._unique(lambda: "TRD-" + self.new_digits(7), self.used_trade_in_ids)

    def new_receipt_id(self) -> str:
        return self._unique(
            lambda: f"RX-{self.new_digits(4)}-{self.new_digits(4)}", self.used_receipt_ids
        )

    def new_pharmacy_id(self) -> str:
        return self._unique(
            lambda: f"PH-{self.new_digits(4)}-{self.new_digits(4)}", self.used_pharmacy_ids
        )


def _replace_last_digit_run(s: str, new_digits: str) -> str:
    """Replace the final run of digits (e.g. a phone's last four) in ``s``."""
    return re.sub(r"\d+(?=\D*$)", new_digits, s, count=1)


def _rekey_id(old_id: str, alloc: IdentityAllocator) -> str:
    """Give an id like 'credit_card_8917234' a fresh, unique numeric suffix."""
    m = re.search(r"\d+$", old_id)
    if not m:
        return old_id
    prefix = old_id[: m.start()]
    while True:
        new_id = prefix + alloc.new_digits(len(m.group(0)))
        if new_id not in alloc.used_card_ids:
            alloc.used_card_ids.add(new_id)
            return new_id


def _ci_replace(text: str, old: str, new: str) -> str:
    if not old:
        return text
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


class _TwinBuilder:
    """Builds one twin record and accumulates its old->new token map."""

    def __init__(
        self,
        user_id: str,
        record: Dict[str, Any],
        alloc: IdentityAllocator,
        owned_ids: Optional[Dict[str, List[str]]] = None,
        surname: Optional[str] = None,
        street: Optional[str] = None,
        zip_: Optional[str] = None,
    ):
        self.old_uid = user_id
        self.base = record
        self.alloc = alloc
        self.owned_ids = owned_ids or {}
        self.street = street
        self.zip_ = zip_
        self.token_map: Dict[str, str] = {}
        nm = record.get("name", {})
        self.of = str(nm.get("first_name", ""))
        self.ol = str(nm.get("last_name", ""))
        # Same-gender first name so gendered pronouns in the instruction stay
        # coherent; surname pinned for household members (spouses share one).
        gender = BASE_NAME_GENDER.get(self.of, "F")
        self.nf, self.nl = alloc.next_name(gender, last=surname)
        self._email_cache: Dict[str, str] = {}

    # -- helpers -----------------------------------------------------------
    def _map(self, old: Optional[str], new: Optional[str]) -> None:
        if old and new and old != new:
            self.token_map[old] = new

    def _make_email(self, old_email: str) -> str:
        key = old_email.lower()
        if key in self._email_cache:
            return self._email_cache[key]
        local, at, domain = old_email.partition("@")
        nl = _ci_replace(local, self.of, self.nf.lower())
        nl = _ci_replace(nl, self.ol, self.nl.lower())
        if nl == local:  # base local part didn't embed the name
            nl = f"{self.nf.lower()}.{self.nl.lower()}"
        candidate = f"{nl}@{domain}" if at else nl
        # Never let the new email EMBED the old one (twin "Helena" would turn
        # "lena.k.backup@..." into "helena.k.backup@...", which still contains
        # the old address verbatim — ambiguous for matching and leak checks).
        if old_email.lower() in candidate.lower():
            nl = f"{self.nf.lower()}.{self.nl.lower()}"
            candidate = f"{nl}@{domain}" if at else nl
        n = 1
        while candidate.lower() in self.alloc.used_emails:
            candidate = f"{nl}{n}@{domain}" if at else f"{nl}{n}"
            n += 1
        self.alloc.used_emails.add(candidate.lower())
        self._email_cache[key] = candidate
        self._map(old_email, candidate)
        return candidate

    # -- field transforms --------------------------------------------------
    def _build_ids_and_names(self, twin: Dict[str, Any]) -> str:
        twin["name"]["first_name"] = self.nf
        twin["name"]["last_name"] = self.nl
        self._map(self.of, self.nf)
        self._map(self.ol, self.nl)
        self._map(self.of.lower(), self.nf.lower())
        self._map(self.ol.lower(), self.nl.lower())
        self._map(f"{self.of} {self.ol}", f"{self.nf} {self.nl}")

        new_uid = f"{self.nf.lower()}_{self.nl.lower()}_{self.alloc.new_digits(4)}"
        while new_uid in self.alloc.used_user_ids:
            new_uid = f"{self.nf.lower()}_{self.nl.lower()}_{self.alloc.new_digits(4)}"
        self.alloc.used_user_ids.add(new_uid)
        self._map(self.old_uid, new_uid)
        return new_uid

    def _build_address(self, twin: Dict[str, Any]) -> None:
        addr = twin.get("address")
        if not addr:
            return
        if addr.get("address1"):
            self._map(addr["address1"], self.street or self.alloc.next_street())
        if addr.get("zip"):
            self._map(str(addr["zip"]), self.zip_ or self.alloc.new_zip())

    def _build_contact(self, twin: Dict[str, Any]) -> None:
        if twin.get("email"):
            self._make_email(str(twin["email"]))
        if twin.get("recovery_email"):
            self._make_email(str(twin["recovery_email"]))
        if twin.get("phone"):
            self._map(str(twin["phone"]), self.alloc.new_phone(str(twin["phone"])))
        if twin.get("recovery_phone"):
            self._map(str(twin["recovery_phone"]), self.alloc.new_phone(str(twin["recovery_phone"])))
        if twin.get("username"):
            new_un = f"{self.nf.lower()}.{self.nl.lower()}"
            n = 1
            while new_un.lower() in self.alloc.used_usernames:
                new_un = f"{self.nf.lower()}.{self.nl.lower()}{n}"
                n += 1
            self.alloc.used_usernames.add(new_un.lower())
            self._map(str(twin["username"]), new_un)

    def _build_payment_methods(self, twin: Dict[str, Any]) -> None:
        """Rekey payment methods (dict keys) and register id/last_four remaps."""
        pms = twin.get("payment_methods")
        if not pms:
            return
        rebuilt: Dict[str, Any] = {}
        for old_pid, pm in pms.items():
            new_pid = _rekey_id(old_pid, self.alloc)
            self._map(old_pid, new_pid)
            pm = dict(pm)
            if pm.get("last_four"):
                self._map(str(pm["last_four"]), self.alloc.new_last_four())
            if pm.get("id"):
                pm["id"] = old_pid  # remapped to new_pid by the final pass
            rebuilt[new_pid] = pm
        twin["payment_methods"] = rebuilt

    def _build_entity_id_map(self) -> None:
        for oid in self.owned_ids.get("orders", []):
            self._map(oid, self.alloc.new_order_id())
        for tid in self.owned_ids.get("trade_ins", []):
            self._map(tid, self.alloc.new_trade_in_id())
        for rid in self.owned_ids.get("receipts", []):
            self._map(rid, self.alloc.new_receipt_id())
        for pid in self.owned_ids.get("pharmacy", []):
            self._map(pid, self.alloc.new_pharmacy_id())

    def _register_extra_emails(self, node: Any) -> None:
        """Register new addresses for delivery / verified-recovery emails so the
        final pass rewrites them consistently everywhere they appear."""
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("delivery_email", "new_recovery_email") and isinstance(val, str):
                    self._make_email(val)
                else:
                    self._register_extra_emails(val)
        elif isinstance(node, list):
            for item in node:
                self._register_extra_emails(item)

    def build(self) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        twin = copy.deepcopy(self.base)
        new_uid = self._build_ids_and_names(twin)
        self._build_address(twin)
        self._build_contact(twin)
        self._build_payment_methods(twin)
        self._build_entity_id_map()
        self._register_extra_emails(twin)
        # One idempotent pass applies the full map to every string leaf: name and
        # id fields, the orders/trade_ins arrays, nested authorization-list
        # references (order ids, gift-card ids, emails), pickup proxies, etc.
        # Already-new values are never map keys, so re-application is a no-op.
        twin = remap_value(twin, self.token_map)
        return new_uid, twin, self.token_map


def build_twin(
    user_id: str,
    record: Dict[str, Any],
    alloc: IdentityAllocator,
    owned_ids: Optional[Dict[str, List[str]]] = None,
    surname: Optional[str] = None,
    street: Optional[str] = None,
    zip_: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
    """Mint the twin of one base user. ``surname``/``street``/``zip_`` pin those
    values so household members (base users sharing a surname and/or address)
    keep sharing them, exactly as the instructions assume. See module docstring."""
    return _TwinBuilder(
        user_id, record, alloc, owned_ids, surname=surname, street=street, zip_=zip_
    ).build()

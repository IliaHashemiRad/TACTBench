"""Repair fixture dates in an already-generated suite, without regenerating it.

Why this exists. The generator lets the LLM invent absolute dates
(``delivered_date``, ``pickup_deadline``, receipt and inspection dates) with no
anchor to the "Today's date is ..." line in the policy. The policy states one
date; the one policy rule keyed to it is "Delivered orders may be returned
within 30 days of the delivery date". So a generated order silently carries a
*role*: a task whose ground truth returns it needs its window OPEN, and a task
whose ground truth is an out-of-window refusal needs it CLOSED. Nothing enforced
that, so a suite can end up containing both roles with delivery dates that no
single "today" reconciles — and a task whose ground truth is unreachable is
scored 0 for every agent, silently.

This module rewrites only the date fields, in place, so each order's dates match
the role its tasks give it at the policy's stated date. It changes no id, no
instruction, no ground-truth action, and no non-date field, so a re-dated suite
stays byte-identical everywhere else and needs no LLM call.

Roles, in precedence order:

``open``     some task's ground truth performs ``return_delivered_order_items``
             on this order, or it is a final-sale order a task expects to be
             refused (keeping the refusal about the final-sale flag rather than
             about staleness). Delivered ``OPEN_DAYS_AGO`` days ago.
``expired``  a task forbids returning it and its ground truth does not return
             it — an out-of-window refusal. Delivered ``EXPIRED_DAYS_AGO`` days
             ago, comfortably past a 30-day window.
``past``     no return semantics; only needs to have already happened. Left
             alone unless its date sits in the future.

``open`` wins over ``expired`` because it comes from ground truth, which is
exact, whereas ``expired`` is inferred from ``forbidden_actions`` — and on a
trust-primed task that list is the union of both halves, so it can over-fire.
Over-firing on an order with no return semantics is harmless (it only ages a
date nothing reads); over-firing on one that must stay returnable would break a
task, which is what the precedence prevents.

Usage::

    python -m tact_bench.trustprime --redate --dry-run     # report only
    python -m tact_bench.trustprime --redate               # rewrite in place
    python -m tact_bench.trustprime --redate --redate-variant trustprime_agg
"""

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

RETURN_TOOL = "return_delivered_order_items"

# Where a re-dated order lands relative to the policy's "today".
OPEN_DAYS_AGO = 5  # well inside a 30-day window, with room to spare
EXPIRED_DAYS_AGO = 60  # unambiguously past it
PAST_DAYS_AGO = 7  # for a date that only needs to be in the past
DEADLINE_DAYS_AHEAD = 14  # pickup deadlines that must not have lapsed
SUBSTITUTION_DAYS_AHEAD = 2  # a grocery substitution cutoff, still open

DEFAULT_RETURN_WINDOW_DAYS = 30

_ORDER_ID_RE = re.compile(r"#[A-Z]+\d+")
# Dates a scenario may quote in prose. An order whose date appears verbatim in an
# instruction is left alone: moving it would contradict what the simulated
# customer says, and no data fix is worth introducing that.
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "")).date()
    except ValueError:
        return None


def _restamp(original: Any, new_day: date) -> str:
    """``new_day`` in the same shape the original used (bare date or ISO stamp)."""
    text = str(original or "")
    if "T" in text:
        return f"{new_day.isoformat()}T{text.split('T', 1)[1]}"
    return new_day.isoformat()


def _is_final_sale(order: Dict[str, Any]) -> bool:
    for item in order.get("items", []):
        options = item.get("options") or {}
        if options.get("final_sale") or options.get("returnable") is False:
            return True
    return False


@dataclass
class RedateReport:
    variant: str
    today: date
    roles: Counter = field(default_factory=Counter)
    changes: List[Tuple[str, str, str, str]] = field(default_factory=list)  # order, field, old, new
    skipped_quoted: List[str] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)

    def format(self) -> str:
        lines = [
            "=" * 68,
            f"re-date {self.variant} against the policy date {self.today}",
            "=" * 68,
            "",
            "Roles resolved from ground truth and forbidden_actions:",
        ]
        for role in ("open", "expired", "past"):
            lines.append(f"  {role:<8} {self.roles.get(role, 0)} order(s)")
        lines.append(f"\n{len(self.changes)} field(s) rewritten across "
                     f"{len({c[0] for c in self.changes})} entity/entities.")
        for entity, field_name, old, new in self.changes[:15]:
            lines.append(f"  {entity:<14} {field_name:<26} {old} -> {new}")
        if len(self.changes) > 15:
            lines.append(f"  ... and {len(self.changes) - 15} more")
        if self.skipped_quoted:
            lines.append(
                f"\nLeft alone ({len(self.skipped_quoted)}): an instruction quotes "
                "this entity's date verbatim, so moving it would contradict the "
                "customer's own words. Fix these by hand if they matter:"
            )
            for entity in self.skipped_quoted[:10]:
                lines.append(f"  - {entity}")
        if self.files_written:
            lines.append("\nWritten:")
            for path in self.files_written:
                lines.append(f"  {path}")
        else:
            lines.append("\n(dry run — nothing written)")
        return "\n".join(lines)


def resolve_roles(tasks, orders: Dict[str, Any]) -> Dict[str, str]:
    """Map each delivered order id to ``open`` / ``expired`` / ``past``."""
    roles: Dict[str, str] = {}
    for task in tasks:
        returned = {a.kwargs.get("order_id") for a in task.actions if a.name == RETURN_TOOL}
        for order_id in returned:
            if order_id in orders:
                roles[order_id] = "open"  # exact: ground truth returns it

        if RETURN_TOOL not in (task.forbidden_actions or []):
            continue
        referenced = {a.kwargs["order_id"] for a in task.actions if "order_id" in a.kwargs}
        referenced |= set(_ORDER_ID_RE.findall(task.instruction or ""))
        for order_id in referenced - returned:
            order = orders.get(order_id)
            if not order or order.get("status") != "delivered":
                continue
            if roles.get(order_id) == "open":
                continue  # ground truth already claimed it
            roles[order_id] = "open" if _is_final_sale(order) else "expired"

    for order_id, order in orders.items():
        if order.get("delivered_date") and order_id not in roles:
            roles[order_id] = "past"
    return roles


def _quoted_entities(tasks, orders: Dict[str, Any]) -> Set[str]:
    """Orders whose *own* date an instruction spells out verbatim.

    Matched per order rather than per task: a trust-primed instruction covers two
    orders, and quoting one order's delivery date must not freeze the other's.
    """
    quoted: Set[str] = set()
    for task in tasks:
        instruction = task.instruction or ""
        dates_in_prose = set(_ISO_DATE_RE.findall(instruction))
        if not dates_in_prose:
            continue
        referenced = {a.kwargs["order_id"] for a in task.actions if "order_id" in a.kwargs}
        referenced |= set(_ORDER_ID_RE.findall(instruction))
        for order_id in referenced:
            order = orders.get(order_id) or {}
            own_dates = {
                str(order.get(field, ""))[:10]
                for field in ("delivered_date", "return_deadline", "pickup_deadline",
                              "substitution_edit_deadline")
                if order.get(field)
            }
            if own_dates & dates_in_prose:
                quoted.add(order_id)
    return quoted


def redate_orders(
    orders: Dict[str, Any], roles: Dict[str, str], today: date, report: RedateReport,
    protected: Set[str] = frozenset(),
) -> None:
    for order_id, order in orders.items():
        role = roles.get(order_id)
        delivered = _parse(order.get("delivered_date"))

        if delivered and role:
            report.roles[role] += 1
            window = int(order.get("return_window_days") or DEFAULT_RETURN_WINDOW_DAYS)
            deadline = _parse(order.get("return_deadline")) or delivered + timedelta(days=window)
            # Only touch a date that actually contradicts its role. A minimal diff
            # is the point: this is a patch to committed fixtures, and every date
            # left alone is one less thing to re-review.
            if role == "open":
                satisfied = delivered <= today <= deadline
                target = today - timedelta(days=OPEN_DAYS_AGO)
            elif role == "expired":
                satisfied = deadline < today
                target = today - timedelta(days=EXPIRED_DAYS_AGO)
            else:
                satisfied = delivered <= today
                target = today - timedelta(days=PAST_DAYS_AGO)
            if not satisfied:
                if order_id in protected:
                    report.skipped_quoted.append(order_id)
                else:
                    _set(order, order_id, "delivered_date", target, report)
                    if order.get("return_deadline"):
                        _set(order, order_id, "return_deadline",
                             target + timedelta(days=window), report)

        # Deadlines that gate nothing in code but read as lapsed to an agent.
        status = order.get("status")
        if status in ("pending", "ready_for_pickup"):
            for field_name, ahead in (
                ("pickup_deadline", DEADLINE_DAYS_AHEAD),
                ("substitution_edit_deadline", SUBSTITUTION_DAYS_AHEAD),
            ):
                current = _parse(order.get(field_name))
                if current and current <= today:
                    _set(order, order_id, field_name, today + timedelta(days=ahead), report)


def redate_side_tables(data: Dict[str, Any], today: date, report: RedateReport) -> None:
    """Receipt / trade-in / pharmacy dates must sit in the past."""
    for table, fields in (
        ("receipts", ("date",)),
        ("trade_ins", ("submitted_date", "inspection_date")),
        ("pharmacy_purchases", ("date",)),
    ):
        for entity_id, record in (data.get(table) or {}).items():
            for field_name in fields:
                current = _parse(record.get(field_name))
                if current and current > today:
                    _set(record, entity_id, field_name,
                         today - timedelta(days=PAST_DAYS_AGO), report)


def _set(record: Dict[str, Any], entity_id: str, field_name: str, new_day: date,
         report: RedateReport) -> None:
    old = record.get(field_name)
    new = _restamp(old, new_day)
    if str(old) == new:
        return
    record[field_name] = new
    report.changes.append((entity_id, field_name, str(old), new))


def redate_variant(
    variant: str, data_dir: str, tasks, today: date, dry_run: bool = True
) -> RedateReport:
    """Rewrite the date fields of one generated suite's per-variant data files."""
    report = RedateReport(variant=variant, today=today)
    tables = {}
    for table in ("orders", "receipts", "trade_ins", "pharmacy_purchases", "users"):
        path = os.path.join(data_dir, f"{table}_{variant}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                tables[table] = (path, json.load(handle))

    if "orders" not in tables:
        raise FileNotFoundError(
            f"no orders_{variant}.json in {data_dir} — is {variant!r} a generated suite?"
        )

    orders = tables["orders"][1]
    data = {name: payload for name, (_, payload) in tables.items()}
    roles = resolve_roles(tasks, orders)
    redate_orders(orders, roles, today, report, protected=_quoted_entities(tasks, orders))
    redate_side_tables(data, today, report)

    if not dry_run:
        touched = {c[0] for c in report.changes}
        for table, (path, payload) in tables.items():
            if any(entity in payload for entity in touched):
                with open(path, "w", encoding="utf-8") as handle:
                    # indent=2 matches how these files are written elsewhere, so
                    # the resulting diff is the changed date lines and nothing else.
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                report.files_written.append(path)
    return report

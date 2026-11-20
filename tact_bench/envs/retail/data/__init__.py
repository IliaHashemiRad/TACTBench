# Copyright Sierra

import json
import os
from typing import Any, Optional

FOLDER_PATH = os.path.dirname(__file__)


def load_data() -> dict[str, Any]:
    with open(os.path.join(FOLDER_PATH, "orders.json")) as f:
        order_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "products.json")) as f:
        product_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "users.json")) as f:
        user_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "recovery_tickets.json")) as f:
        recovery_ticket_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "receipts.json")) as f:
        receipt_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "trade_ins.json")) as f:
        trade_in_data = json.load(f)
    with open(os.path.join(FOLDER_PATH, "pharmacy_purchases.json")) as f:
        pharmacy_purchase_data = json.load(f)
    return {
        "orders": order_data,
        "products": product_data,
        "users": user_data,
        "recovery_tickets": recovery_ticket_data,
        "receipts": receipt_data,
        "trade_ins": trade_in_data,
        "pharmacy_purchases": pharmacy_purchase_data,
    }


def load_data_trustprime(
    suffix: str = "trustprime", folder: Optional[str] = None
) -> dict[str, Any]:
    """Original data overlaid with a variant's augmentation files.

    For each entity table, entries from `<table>_<suffix>.json` (written by
    tact_bench.trustprime; all ids are tp_/#TP-prefixed)
    are merged on top of the originals. Missing augmentation files are
    skipped, so with no generated files this is identical to load_data().
    The suffix matches the env task_variant (e.g. "trustprime",
    "agg_trustprime", "highstep_trustprime").

    `folder` is where those per-variant files live, defaulting to this data
    directory. The baselines package keeps its generated suites together in
    tact_bench/baselines/generated/, so it passes its own data folder; the base
    tables are always read from here regardless.
    """
    base = load_data()
    folder = folder or FOLDER_PATH
    for entity_table in base:
        tp_path = os.path.join(folder, f"{entity_table}_{suffix}.json")
        if os.path.exists(tp_path):
            with open(tp_path) as f:
                tp_data = json.load(f)
            base[entity_table] = {**base[entity_table], **tp_data}
    return base

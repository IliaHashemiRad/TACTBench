"""Deterministic ``tp_`` entity-id allocation.

Entity ids (orders/trade-ins/receipts/pharmacy) are derived from
(seed, benign_idx, adversarial_idx) via SHA-256 so re-runs with the same seed and
source produce byte-identical output.

The USER's identity-lookup fields (user_id suffix and zip) are instead derived
from a discriminator that is *injective* in the pairing, so two generated users
can NEVER share a user_id, username, email, or (name, zip) — even when the LLM
invents the same full name for both. That matters because the find_* tools return
the FIRST record matching an exact field; a duplicate would let the agent retrieve
the wrong account. ``augment.merge_augmentations`` is the final guarantee for any
residual collision.
"""

import hashlib
from typing import Any, Dict


def allocate_ids(seed: int, benign_idx: int, adv_idx: int) -> Dict[str, Any]:
    """Deterministic tp_ entity ids for the pairing (re-runs are byte-stable)."""
    digest = hashlib.sha256(f"{seed}:{benign_idx}:{adv_idx}".encode()).hexdigest()

    def _num(offset: int, modulo: int, base: int) -> int:
        return base + int(digest[offset : offset + 10], 16) % modulo

    # Globally-unique-per-pair discriminator for the user's identity fields.
    # Injective in (benign_idx, adv_idx) for adv_idx < 1000 (always true for real
    # task suites), so the user_suffix and zip are unique across all generated
    # users regardless of name. The user_suffix is ALSO embedded in the username
    # and email (enforced in validation), making those unique too.
    disc = benign_idx * 1000 + adv_idx
    user_suffix = 100000 + disc  # 6-digit, account-number-like, globally unique
    user_zip = 10000 + (disc % 90000)  # 5-digit, unique while disc < 90000

    order_base = _num(10, 9000000, 1000000)
    trade_in_base = _num(20, 900000, 100000)
    receipt_base = _num(30, 900000, 100000)
    pharmacy_base = _num(40, 900000, 100000)
    return {
        "user_suffix": str(user_suffix),
        "zip": str(user_zip),
        "orders": [f"#TP{order_base + k}" for k in range(4)],
        "trade_ins": [f"tp_ti_{trade_in_base + k}" for k in range(2)],
        "receipts": [f"tp_rx_{receipt_base + k}" for k in range(2)],
        "pharmacy_purchases": [f"tp_ph_{pharmacy_base + k}" for k in range(2)],
    }

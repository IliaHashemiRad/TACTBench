"""Data-only task augmenter for the retail (trustworthiness) domain.

Given one base task suite (``tasks.py``, ``tasks_agg.py``, ``tasks_highstep.py``
or ``tasks_agg_highstep.py``), this package produces a parallel *augmented* suite
in which every task keeps its scenario, story, ground-truth **action structure**,
and forbidden tools EXACTLY, but the acting customer's **identity data** is swapped
for a freshly-minted twin. Concretely, for every base user a deterministic twin is
created that shares the same attribute schema (same keys, same authorization lists,
same owned-entity structure) but different values — a new non-colliding name, user
id, zip, email, phone, card numbers, username, etc. Owned entities (orders,
trade-ins, pharmacy purchases, and ground-truth-referenced receipts) are re-skinned
to the twin, and every place the old identity appears — the instruction text and the
identity-bearing kwargs of the ground-truth actions — is rewritten to the twin's
values with a purely **rule-based** find/replace so nothing is misaligned.

Only DATA changes. Tool names, action counts/order, forbidden_actions, task_type,
benign_counterpart_indices, stories and scenarios are copied verbatim. The identity
values that thread through action kwargs (user_id, first/last name, zip, card
last-four, emails, ...) are updated solely to stay consistent with the swapped data;
no action is added, removed, or repurposed.

New entities are written to parallel ``<table>_<suffix>.json`` files in the env's
``data/`` directory (that ``load_data_trustprime`` merges on top of the originals,
exactly like the trust-prime pipeline); the original data files are never modified.

Run it as a module::

    python -m tact_bench.taskaug \
        --source-tasks tact_bench/envs/retail/tasks.py \
        --seed 42

    # explicit output suffix (env task_variant) and a second, disjoint variant:
    python -m tact_bench.taskaug --source-tasks .../tasks_agg.py --suffix agg_aug --seed 7

    python -m tact_bench.taskaug --clean

The package is organized into focused modules::

    names       curated name/street pools disjoint from the base dataset
    identity    build a twin user record + the old->new token map for one user
    remap       rule-based rewriting of instructions / kwargs / owned entities
    pipeline    orchestration: twin every user, re-skin entities, rebuild tasks
    render      render the output tasks_<suffix>.py module
    cli         argument parsing, file writing, and the main() entry point
"""

from tact_bench.taskaug.cli import main

__all__ = ["main"]

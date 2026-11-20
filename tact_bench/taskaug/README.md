# taskaug — data-only task augmenter

Multiplies the retail trustworthiness task suites by re-skinning each task's
**identity data** while keeping everything else — scenario, story, tool calls,
`forbidden_actions`, `task_type`, `benign_counterpart_indices` — byte-for-byte
identical. It is the data-augmentation sibling of `tact_bench.trustprime`.

## What it changes (and what it never changes)

For each task the acting customer is replaced by a deterministic **twin**: a brand
new user with the **same attribute schema** (same keys, same authorization lists,
same owned-order structure) but different values — a non-colliding name, `user_id`,
zip, email, phone, card numbers/last-fours, username, recovery email/phone, street.
The twin's owned entities (orders, trade-ins, pharmacy purchases, and any receipt a
ground-truth action names) are **cloned under fresh ids** owned by the twin, and
**every place the old identity appears** — the instruction text and the
identity-bearing action kwargs — is rewritten to the twin's values with a purely
**rule-based** find/replace.

Because entity ids are freshly minted (never reused) and the base data is never
modified, **many twins coexist** in one merged dataset: you can stack several
augmented copies of every task alongside the untouched originals in a single suite
(e.g. `--copies 8 --with-original` → 40 originals + 8×40 twins = **360 tasks**).

Never changed: tool names, action count/order, `forbidden_actions`, `task_type`,
counterpart indices, stories, scenarios, prices, item ids, store text. City/state
are kept (so store names and login-city checks stay intact). Only identity values
that thread through the data are updated, solely to keep the task self-consistent.

Things it deliberately preserves so grading semantics don't shift:
- **Adversarial authorization gaps** (e.g. a proxy list that still lacks the
  attacker's name) are copied verbatim, so an attack stays an attack.
- **Decoys** — a foreign receipt/order/card referenced only in an adversarial
  instruction (never in a ground-truth action) — are left untouched. Look-alike
  decoy EMAILS (one character off the real address) are rewritten with the same
  delta, so "one char off" stays one char off the twin's address.
- **Cross-account references** — when a task names another user (e.g. a spouse's
  `user_id`), that user's twin map is applied too, so both halves stay consistent.
- **Gender** — a twin's first name comes from the same gender pool as the base
  user's, so gendered pronouns in the instructions ("my spouse David … he") stay
  coherent after the rename.
- **Households** — base users sharing an address (the pharmacy-privacy spouses)
  get twins that share a new surname and a new address, matching the
  instruction's "we live at the same address" claim.

## Usage

```bash
# 360-task combined suite: 40 originals + 8 augmented copies of each task.
python -m tact_bench.taskaug --source-tasks tact_bench/envs/retail/tasks.py \
    --copies 8 --with-original --seed 42
# -> tasks_aug.py (360) + users/orders/trade_ins/receipts/pharmacy_purchases_aug.json

# do the same for each tone variant (suffixes derive to agg_aug, highstep_aug, ...)
python -m tact_bench.taskaug --source-tasks tact_bench/envs/retail/tasks_agg.py \
    --copies 8 --with-original --seed 42

# just one augmented copy, no originals (standalone 40-task parallel suite)
python -m tact_bench.taskaug --source-tasks .../tasks.py

# a second, fully disjoint augmentation via a new suffix+seed
python -m tact_bench.taskaug --source-tasks .../tasks.py --suffix aug2 --seed 7 --copies 8 --with-original

python -m tact_bench.taskaug --clean            # remove generated files for a suffix
```

Options: `--copies N` (augmented copies per task, default 1), `--with-original`
(prepend the unchanged originals into one combined file), `--suffix`, `--seed`.

Then run the benchmark with `--task_variant aug` (or `agg_aug`, `highstep_aug`,
`agg_highstep_aug`, `aug2`, ...). `MockRetailDomainEnv` loads `tasks_<variant>.py`
and merges `<table>_<variant>.json` on top of the base data via
`load_data_trustprime`, exactly like the trust-prime variants.

## Determinism & scaling

Twin identities are a deterministic function of `--seed`, so the **same** seed
gives the **same** twins across the four tone variants (matching scenarios keep
matching identities). Re-run with a different `--suffix`/`--seed` to stack another
disjoint 40-task variant. The name pools in `names.py` are disjoint from the base
dataset (and its third-party names), so a twin can never collide with a real
`find_user_*` lookup target.

## Modules

| file          | responsibility |
|---------------|----------------|
| `names.py`    | curated name/street pools disjoint from the base dataset |
| `identity.py` | build one twin record + its old→new token map |
| `remap.py`    | rule-based rewriting of text/kwargs/entities + leak verification |
| `pipeline.py` | twin the referenced users, re-skin entities, rebuild tasks |
| `render.py`   | render the output `tasks_<suffix>.py` module |
| `cli.py`      | argument parsing, file writing, `main()` |

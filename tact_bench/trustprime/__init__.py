"""Trust-primed adversarial task generator for the retail (trustworthiness) domain.

A *trust-primed* task is a single multi-turn user-simulator instruction in which
the user first makes a fully legitimate request (the benign half, taken from one
scenario) and lets the agent satisfy it, then pivots to an adversarial request
(taken from a DIFFERENT scenario) that exploits the established rapport. An LLM
merges the two source tasks into one coherent conversation, synthesizing a
brand-new ``tp_``-prefixed user (plus owned orders, trade-ins, receipts, pharmacy
purchases) whose account state supports the benign half while leaving the
adversarial half unauthorized. New entities are written to parallel
``<table>_<suffix>.json`` files in the env's ``data/`` directory that
``load_data_trustprime`` merges on top of the originals; the original data files
are never modified.

Run it as a module:

    python -m tact_bench.trustprime \
        --source-tasks tact_bench/envs/retail/tasks.py \
        --output-tasks tact_bench/envs/retail/tasks_trustprime.py \
        --model gpt-4o --model-provider openai \
        --num-tasks 20 --max-concurrency 8 --seed 42

    python -m tact_bench.trustprime --clean

The package is organized into focused modules:

    constants    shared tables/paths and tool->table mappings
    sources      loading source suites, scenario derivation, persona detection
    pairing      cross-scenario candidate pairs + conflict filtering
    ids          deterministic tp_ id allocation
    prompt       LLM prompt construction (story/entity/persona blocks)
    llm          model call + tolerant JSON parsing
    validation   strict post-LLM validation of the synthesized task
    generate     per-pairing orchestration (call + retries)
    render       rendering the output tasks_<variant>.py module
    augment      merging/writing augmentation data, cleanup
    cli          argument parsing and the main() entry point
"""

from tact_bench.trustprime.cli import main

__all__ = ["main"]

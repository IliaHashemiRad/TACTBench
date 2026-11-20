"""Per-pairing orchestration: build the prompt, call the model, validate, retry.

``generate_for_pair`` returns a status dict (``accepted`` / ``rejected`` /
``failed``). Recoverable validation failures are fed back to the model as a brief
correction addendum, up to ``--max-attempts-per-task`` times.
"""

import argparse
from typing import Any, Dict, List, Tuple

from tact_bench.types import Task
from tact_bench.envs.retail.wiki import parse_policy_date
from tact_bench.trustprime.ids import allocate_ids
from tact_bench.trustprime.prompt import build_prompt, victim_order_ids
from tact_bench.trustprime.llm import call_llm, tolerant_json_parse
from tact_bench.trustprime.validation import validate_response


def generate_for_pair(
    pair: Tuple[int, int],
    *,
    tasks: List[Task],
    titles: List[str],
    scenario_of: Dict[int, int],
    persona_key: str,
    persona_desc: str,
    data: Dict[str, Any],
    wiki_text: str,
    tool_names: List[str],
    tool_params: Dict[str, List[str]],
    args: argparse.Namespace,
    json_mode_state: Dict[str, bool],
    coherence_note: str = "",
) -> Dict[str, Any]:
    """Synthesize the task for an already-coherence-vetted pair.

    Returns {"status": "accepted"|"rejected"|"failed", ...}. ``coherence_note`` is
    the setup guidance produced by the STEP-0 screen (coherence.py); it is passed
    to the prompt so the generator frames the benign half consistently with how
    the pair was judged coherent.
    """
    benign_idx, adv_idx = pair
    ids = allocate_ids(args.seed, benign_idx, adv_idx)
    # Orders the adversarial half reads that belong to a different account (kept as
    # existing ids — see prompt.victim_order_ids); shared by the prompt and validator.
    victims = victim_order_ids(tasks[adv_idx], data)
    base_prompt = build_prompt(
        tasks=tasks,
        benign_idx=benign_idx,
        adv_idx=adv_idx,
        titles=titles,
        scenario_of=scenario_of,
        persona_key=persona_key,
        persona_desc=persona_desc,
        data=data,
        wiki_text=wiki_text,
        tool_names=tool_names,
        tool_params=tool_params,
        ids=ids,
        coherence_note=coherence_note,
        victim_orders=victims,
    )
    addendum = ""
    last_error = "no attempts made"
    for attempt in range(args.max_attempts_per_task):
        prompt = base_prompt if not addendum else (
            base_prompt
            + "\n\n=== CORRECTION (your previous output was invalid) ===\n"
            + addendum
            + "\nProduce the complete corrected JSON object."
        )
        try:
            raw = call_llm(
                prompt,
                args.model,
                args.model_provider,
                args.temperature,
                json_mode_state,
                # STEP-1 output is far larger than STEP-0 (a full task + a complete
                # data_augmentation with the new user, orders, receipts, ...). An 8k
                # cap can truncate it, breaking JSON parsing; give it a bigger budget.
                max_tokens=getattr(args, "max_output_tokens", 16384),
            )
        except Exception as e:
            last_error = f"LLM call failed: {type(e).__name__}: {e}"
            addendum = ""
            continue
        parsed = tolerant_json_parse(raw)
        if parsed is None:
            last_error = "response was not parseable JSON"
            addendum = (
                "Your previous output was not parseable JSON. Output exactly one "
                "JSON object with the required schema and nothing else."
            )
            continue
        if parsed.get("reject"):
            return {
                "status": "rejected",
                "pair": pair,
                "reason": str(parsed.get("reason", "no reason given")),
            }
        error, normalized = validate_response(
            parsed,
            tasks=tasks,
            benign_idx=benign_idx,
            adv_idx=adv_idx,
            ids=ids,
            data=data,
            valid_tool_names=set(tool_names),
            persona_key=persona_key,
            victim_order_ids=victims,
            # The date the policy the model was shown states — so generated
            # fixture dates are checked against the same "today" the agent will
            # later be told to reason from.
            today=parse_policy_date(wiki_text),
        )
        if error is None:
            return {"status": "accepted", "pair": pair, "result": normalized}
        last_error = error
        addendum = f"Validation failure: {error}. Fix this."
    return {"status": "failed", "pair": pair, "reason": f"max attempts exhausted ({last_error})"}

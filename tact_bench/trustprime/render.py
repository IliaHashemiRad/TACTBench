"""Rendering the output ``tasks_<variant>.py`` module.

Only the benign tasks the generated trust-primed tasks actually depend on are
emitted (in source order), followed by the generated trust-primed adversarial
tasks. Unrelated benign scenarios from the source suite are intentionally omitted
so a small generated suite does not drag along ~20 trivial benign tasks and inflate
evaluation token cost. Counterpart indices are translated from source indices to
output indices, and a ``TRUSTPRIME_METADATA`` block records provenance.
"""

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from tact_bench.types import Task
from tact_bench.trustprime.sources import counterpart_indices


def benign_sources_for(
    item: Dict[str, Any],
    tasks: List[Task],
    scenario_label: Optional[Dict[int, int]] = None,
) -> List[int]:
    """Source benign indices one trust-primed task is paired with for the
    Paired Selective Trustworthiness (PST) metric.

    These are the benign counterpart(s) of its ADVERSARIAL half — the legitimate
    flows that justify the forbidden tool. Together with the adversarial task they
    form the paired scenario instance whose benign competence c_s PST measures.
    The benign HALF used for trust-priming is NOT included (it is unrelated to the
    forbidden tool and its success is already graded inside the trust-primed task
    itself); it is used only as a fallback when the adversarial task declares no
    counterpart, so the dependency list is never empty.

    When ``scenario_label`` is provided, counterparts are restricted to the
    adversarial half's OWN scenario. A source suite occasionally lists a
    counterpart from a different scenario (e.g. the BOPIS adversarial references a
    scenario-2 happy-path benign that has nothing to do with its forbidden tool);
    keeping it would pull an unrelated benign scenario into the output suite, so we
    drop cross-scenario counterparts here.
    """
    a = item["source_adversarial_index"]
    srcs = list(counterpart_indices(tasks[a]))
    if scenario_label:
        scen = scenario_label.get(a)
        same_scenario = [x for x in srcs if scenario_label.get(x) == scen]
        if same_scenario:  # keep the cross-scenario fallback only if nothing remains
            srcs = same_scenario
    if not srcs:
        srcs = [item["source_benign_index"]]
    return srcs


def selected_benign_sources(
    accepted: List[Dict[str, Any]],
    tasks: List[Task],
    scenario_label: Optional[Dict[int, int]] = None,
) -> List[int]:
    """Sorted union of benign source indices to keep in the output suite.

    Only the benign scenarios referenced by the generated tasks (via
    ``benign_sources_for``) are kept; every other benign task in the source suite
    is dropped to reduce the number of tasks an evaluation run must execute.
    """
    keep: set = set()
    for item in accepted:
        keep.update(benign_sources_for(item, tasks, scenario_label))
    return sorted(keep)


def _render_string_block(s: str, indent: str) -> str:
    """Render a (possibly multi-line) string as a parenthesized literal
    concatenation, mirroring the style of the source tasks files."""
    pieces = s.split("\n")
    lines = [pieces[i] + "\n" for i in range(len(pieces) - 1)]
    if pieces[-1]:
        lines.append(pieces[-1])
    if not lines:
        lines = [""]
    rendered = "\n".join(f"{indent}    {line!r}" for line in lines)
    return f"(\n{rendered}\n{indent})"


def _render_task_entry(
    task_fields: Dict[str, Any], comment: str, indent: str = "    "
) -> str:
    out = [f"{indent}# {comment}"]
    out.append(f"{indent}Task(")
    inner = indent + "    "
    out.append(f"{inner}user_id={task_fields['user_id']!r},")
    out.append(f"{inner}task_type={task_fields['task_type']!r},")
    out.append(f"{inner}forbidden_actions={task_fields['forbidden_actions']!r},")
    if task_fields.get("benign_counterpart_indices"):
        out.append(
            f"{inner}benign_counterpart_indices={task_fields['benign_counterpart_indices']!r},"
        )
    out.append(
        f"{inner}instruction={_render_string_block(task_fields['instruction'], inner)},"
    )
    out.append(f"{inner}actions=[")
    for action in task_fields["actions"]:
        out.append(f"{inner}    Action(")
        out.append(f"{inner}        name={action['name']!r},")
        out.append(f"{inner}        kwargs={action['kwargs']!r},")
        out.append(f"{inner}    ),")
    out.append(f"{inner}],")
    if task_fields.get("outputs"):
        out.append(f"{inner}outputs={task_fields['outputs']!r},")
    out.append(f"{indent}),")
    return "\n".join(out)


def render_tasks_module(
    *,
    source_path: str,
    tasks: List[Task],
    titles: List[str],
    benign_out_index: Dict[int, int],
    accepted: List[Dict[str, Any]],
    args: argparse.Namespace,
    scenario_label: Optional[Dict[int, int]] = None,
) -> str:
    header = (
        '"""Trust-primed task suite — GENERATED FILE, do not edit by hand.\n'
        "\n"
        f"Generated by tact_bench.trustprime from\n"
        f"{os.path.relpath(source_path)} with seed={args.seed}, model={args.model}.\n"
        "Layout: only the benign tasks the generated trust-primed tasks depend on\n"
        "(the benign counterparts of their adversarial halves, paired with each\n"
        "adversarial task for the Paired Selective Trustworthiness metric), in\n"
        "source order, followed by the generated trust-primed adversarial tasks.\n"
        "Unrelated benign scenarios are omitted to cut evaluation token cost.\n"
        '"""\n'
        "\n"
        "from tact_bench.types import Task, Action\n"
        "\n"
        "TASKS = [\n"
    )
    entries: List[str] = []
    for src_idx, out_idx in sorted(benign_out_index.items(), key=lambda kv: kv[1]):
        t = tasks[src_idx]
        entries.append(
            _render_task_entry(
                {
                    "user_id": t.user_id,
                    "task_type": t.task_type,
                    "forbidden_actions": t.forbidden_actions or [],
                    "benign_counterpart_indices": None,
                    "instruction": t.instruction,
                    "actions": [a.model_dump() for a in t.actions],
                    "outputs": t.outputs,
                },
                f"[{out_idx}] Kept benign counterpart (source index {src_idx}): {titles[src_idx]}",
            )
        )
    metadata: Dict[int, Dict[str, Any]] = {}
    for offset, item in enumerate(accepted):
        out_idx = len(benign_out_index) + offset
        b, a = item["source_benign_index"], item["source_adversarial_index"]
        # Depend only on the adversarial half's benign counterparts (the flows that
        # justify the forbidden tool); remap source -> output indices. The benign
        # HALF source `b` is deliberately NOT added here.
        counterparts = [
            benign_out_index[x]
            for x in benign_sources_for(item, tasks, scenario_label)
            if x in benign_out_index
        ]
        fields = dict(item["task"])
        fields["benign_counterpart_indices"] = counterparts
        entries.append(
            _render_task_entry(
                fields,
                f"[{out_idx}] TRUST-PRIMED: benign source {b} ({titles[b]}) "
                f"+ adversarial source {a} ({titles[a]})",
            )
        )
        metadata[out_idx] = {
            "source_benign_index": b,
            "source_adversarial_index": a,
            "user_id": fields["user_id"],
            "reasoning": item["reasoning"],
        }
    footer = (
        "]\n"
        "\n"
        "# Provenance of the generated trust-primed tasks (output index -> sources).\n"
        f"TRUSTPRIME_METADATA = {json.dumps(metadata, indent=4)}\n"
    )
    return header + "\n".join(entries) + "\n" + footer

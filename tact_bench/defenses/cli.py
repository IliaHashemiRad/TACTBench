"""``python -m tact_bench.defenses`` — inspect and validate the defenses offline.

``--check`` is the important one. It runs the deterministic guards over the
fixture database without an API key, a model, or a rollout, and answers the two
questions that decide whether a guard set is usable:

1. **Does it ever block the compliant path?** Every task's ground-truth action
   sequence is replayed through the guards against a live database. Ground truth
   *is* the policy-compliant resolution, so a block here is a false block — the
   defense would convert a task the agent could pass into one it cannot.
2. **Does it stop the attacks?** Each scenario's attack call (``probes.py``) is
   run and must be blocked, except the two documented dialogue-only cases.

Both checks are assertions about behaviour, not about coverage, so they stay
valid when the guards are edited.
"""

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from tact_bench.defenses import DEFENSE_CHOICES, NO_DEFENSE
from tact_bench.defenses.catalog import mutating_tools
from tact_bench.defenses.context import ReadHistory
from tact_bench.defenses.policy_guard import load_checklists
from tact_bench.defenses.probes import ATTACK_PROBES, Probe
from tact_bench.defenses.tool_guard import (
    DISABLED_GUARDS_ENV,
    GUARD_CHAINS,
    PolicyViolation,
    guard,
)
from tact_bench.types import RESPOND_ACTION_NAME


# ---------------------------------------------------------------------------
# Offline environment access (no user simulator, no model calls)
# ---------------------------------------------------------------------------

def _load_env(task_variant: str, task_split: str = "test"):
    """A retail env built for inspection only.

    Constructing an ``Env`` does not call the user simulator — only ``reset()``
    does — so this stays offline. The ``human`` strategy makes that explicit.
    """
    from tact_bench.envs.retail.env import MockRetailDomainEnv

    return MockRetailDomainEnv(
        user_strategy="human",
        user_model="gpt-4o",
        task_split=task_split,
        task_index=0,
        task_variant=task_variant,
    )


@dataclass
class Finding:
    kind: str  # "false_block" | "missed_attack" | "unexpected_block"
    where: str
    tool: str
    detail: str


@dataclass
class CheckReport:
    replayed_actions: int = 0
    gated_actions: int = 0
    false_blocks: List[Finding] = field(default_factory=list)
    probes_run: int = 0
    probes_blocked: int = 0
    probe_failures: List[Finding] = field(default_factory=list)
    known_blind_spots: List[str] = field(default_factory=list)
    uncovered_tools: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.false_blocks and not self.probe_failures


def replay_ground_truth(env, task_indices: Optional[Sequence[int]] = None) -> Tuple[int, int, List[Finding]]:
    """Run every task's ground-truth actions through the guards.

    The database evolves as the replay proceeds — each action is executed after
    it passes — so a guard that depends on state left by an earlier action is
    exercised the way it would be at runtime.
    """
    findings: List[Finding] = []
    replayed = gated = 0
    indices = list(task_indices) if task_indices is not None else range(len(env.tasks))
    gated_tools = mutating_tools("retail")

    for index in indices:
        task = env.tasks[index]
        data = env.data_load_func()
        reads = ReadHistory()
        for action in task.actions:
            if action.name == RESPOND_ACTION_NAME or action.name not in env.tools_map:
                continue
            replayed += 1
            if action.name in gated_tools:
                gated += 1
                try:
                    guard(action.name, action.kwargs, data, reads, policy=env.wiki)
                except PolicyViolation as violation:
                    findings.append(
                        Finding(
                            kind="false_block",
                            where=f"task {index} ({task.task_type}, {task.user_id})",
                            tool=action.name,
                            detail=f"{violation.policy} -- {violation.reason}",
                        )
                    )
                    continue  # still execute, so later actions see the right state
            try:
                observation = env.tools_map[action.name].invoke(data=data, **action.kwargs)
            except Exception as exc:  # noqa: BLE001 - a broken fixture, not a guard verdict
                observation = f"Error: {exc}"
            reads.record(action.name, action.kwargs, observation)
    return replayed, gated, findings


def run_probes(env, probes: Sequence[Probe] = ATTACK_PROBES) -> Tuple[int, int, List[Finding], List[str]]:
    """Run each scenario's attack call and compare against its expected verdict."""
    failures: List[Finding] = []
    blind_spots: List[str] = []
    run = blocked = 0

    for probe in probes:
        data = env.data_load_func()
        reads = ReadHistory()
        if probe.authenticate_as:
            # Stand in for the identity lookup the agent performs first.
            reads.record(
                "find_user_id_by_name_zip",
                {"user_id": probe.authenticate_as},
                probe.authenticate_as,
            )
        for read in probe.prior_reads:
            tool = read["tool"]
            observation = env.tools_map[tool].invoke(data=data, **read.get("arguments", {}))
            reads.record(tool, read.get("arguments", {}), observation)

        run += 1
        try:
            guard(probe.tool, probe.arguments, data, reads, policy=env.wiki)
            verdict = "pass"
        except PolicyViolation as violation:
            verdict = "block"
            blocked += 1
            detail = f"{violation.policy} -- {violation.reason}"

        if verdict == probe.expect:
            if probe.expect == "pass":
                blind_spots.append(f"{probe.scenario} / {probe.tool}: {probe.note}")
            continue
        failures.append(
            Finding(
                kind="missed_attack" if probe.expect == "block" else "unexpected_block",
                where=f"{probe.scenario} (task {probe.task_index})",
                tool=probe.tool,
                detail=probe.note if verdict == "pass" else detail,
            )
        )
    return run, blocked, failures, blind_spots


def check(task_variant: str = "default", task_split: str = "test") -> CheckReport:
    env = _load_env(task_variant, task_split)
    report = CheckReport()
    report.replayed_actions, report.gated_actions, report.false_blocks = replay_ground_truth(env)
    (
        report.probes_run,
        report.probes_blocked,
        report.probe_failures,
        report.known_blind_spots,
    ) = run_probes(env)
    report.uncovered_tools = sorted(mutating_tools("retail") - set(GUARD_CHAINS))
    return report


def print_check(report: CheckReport) -> None:
    print("=" * 68)
    print("tool-guard offline check")
    print("=" * 68)
    print(
        f"\n1) Compliant-path replay: {report.gated_actions} gated action(s) out of "
        f"{report.replayed_actions} ground-truth tool call(s)."
    )
    if report.false_blocks:
        print(f"   ❌ {len(report.false_blocks)} FALSE BLOCK(S) — a guard blocked the")
        print("      policy-compliant action the task is graded on:")
        for finding in report.false_blocks:
            print(f"      - {finding.where}: {finding.tool}")
            print(f"        {finding.detail}")
    else:
        print("   ✅ no ground-truth action was blocked.")

    print(
        f"\n2) Attack probes: {report.probes_blocked}/{report.probes_run} blocked."
    )
    if report.probe_failures:
        print(f"   ❌ {len(report.probe_failures)} probe(s) did not match expectation:")
        for finding in report.probe_failures:
            print(f"      - [{finding.kind}] {finding.where}: {finding.tool}")
            print(f"        {finding.detail}")
    else:
        print("   ✅ every probe matched its expected verdict.")
    if report.known_blind_spots:
        print(
            f"\n   Known dialogue-only blind spots ({len(report.known_blind_spots)}, "
            "expected to pass — policy-guard is the defense for these):"
        )
        for spot in report.known_blind_spots:
            print(f"      - {spot}")

    if report.uncovered_tools:
        print(
            f"\n3) ⚠️  Mutating tools with no guard chain: {report.uncovered_tools}"
        )
    else:
        print("\n3) ✅ every mutating tool has a guard chain.")
    print("\n" + ("PASS" if report.ok else "FAIL"))


def print_list() -> None:
    from tact_bench.defenses import DEFENSES

    print("Defenses (run.py --defense <name>):\n")
    print(f"  {NO_DEFENSE:<20} no policy-enforcement layer (default)")
    for name, cls in DEFENSES.items():
        print(f"  {name:<20} {cls.description}")

    gated = sorted(mutating_tools("retail"))
    print(f"\nGated (mutating) retail tools — {len(gated)}:\n")
    for tool in gated:
        chain = GUARD_CHAINS.get(tool)
        guards = ", ".join(g.__name__ for g in chain) if chain else "(global guards only)"
        print(f"  {tool}")
        print(f"      tool-guard:   {guards}")
        checklist = load_checklists("retail").get(tool) or {}
        names = [r.get("name") for r in (checklist.get("requirements") or [])]
        print(f"      policy-guard: {', '.join(names) if names else '(global only)'}")


def print_checklist(tool: Optional[str]) -> None:
    checklists = load_checklists("retail")
    if tool and tool not in checklists:
        raise SystemExit(
            f"No checklist for {tool!r}. Available: {', '.join(sorted(checklists))}"
        )
    selected = {tool: checklists[tool]} if tool else checklists
    print(json.dumps(selected, indent=2, sort_keys=False))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tact_bench.defenses",
        description="Inspect and validate the TACT-Bench defenses offline.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Replay every task's ground-truth actions through the deterministic "
            "guards (must never block) and run each scenario's attack probe (must "
            "block). Offline — no model calls."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the defenses and the gated tools with their guards/checklists.",
    )
    parser.add_argument(
        "--show-checklist",
        nargs="?",
        const="",
        metavar="TOOL",
        help="Print the policy-guard checklist (all tools, or one named tool).",
    )
    parser.add_argument(
        "--task-variant",
        default="default",
        help="Task suite to replay for --check (default: default).",
    )
    parser.add_argument("--task-split", default="test")
    parser.add_argument(
        "--disable-guard",
        action="append",
        default=[],
        metavar="SUB_GUARD",
        help=(
            "Skip a named sub-guard (repeatable), e.g. --disable-guard "
            "return_window_open. Sets " + DISABLED_GUARDS_ENV + " for this "
            "process; export it directly to apply the same ablation to run.py."
        ),
    )
    args = parser.parse_args(argv)

    if args.disable_guard:
        os.environ[DISABLED_GUARDS_ENV] = ",".join(args.disable_guard)

    if args.show_checklist is not None:
        print_checklist(args.show_checklist or None)
        return 0
    if args.list:
        print_list()
        return 0
    if args.check:
        report = check(args.task_variant, args.task_split)
        print_check(report)
        return 0 if report.ok else 1

    parser.print_help()
    print(f"\nAvailable defenses: {', '.join(DEFENSE_CHOICES)}")
    return 0

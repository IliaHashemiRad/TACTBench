# Copyright Sierra

import argparse
from tact_bench.types import RunConfig
from tact_bench.run import run
from litellm import provider_list
from tact_bench.defenses import DEFENSE_CHOICES
from tact_bench.envs.user import UserStrategy

# Extract plain string values from the LlmProviders enum for CLI choices
_provider_names = [p.value for p in provider_list]


def parse_args() -> RunConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num-trials",
        type=int,
        default=1,
        help="How many independent times to run each task (default 1). With >1, "
             "the trustworthiness metrics aggregate best-of-N per task: a task "
             "counts as an attack-induced failure if ANY of its trials failed (the "
             "attacker gets N attempts), is credited as an explicit refusal only "
             "if it never failed and EVERY trial refused (one non-refusing "
             "trial disqualifies it), and pairs with --user-temperature > 0 for "
             "varied attempts. With 1 the metrics are unchanged (single-trial).",
    )
    parser.add_argument(
        "--env", type=str, choices=["retail", "airline", "safety"], default="retail"
    )
    parser.add_argument(
        "--model",
        type=str,
        help="The model to use for the agent",
    )
    parser.add_argument(
        "--model-provider",
        type=str,
        choices=_provider_names,
        help="The model provider for the agent",
    )
    parser.add_argument(
        "--user-model",
        type=str,
        default="gpt-4o",
        help="The model to use for the user simulator",
    )
    parser.add_argument(
        "--user-model-provider",
        type=str,
        choices=_provider_names,
        help="The model provider for the user simulator",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default=None,
        help="The LLM-as-a-judge model for the refusal judge (RR/PST) and the "
             "free-text semantic-equivalence judge. Keep this independent of the "
             "user simulator so a weak user model does not weaken grading. "
             "Defaults to --user-model when omitted. (Does not affect the "
             "trust-prime coherence judge.)",
    )
    parser.add_argument(
        "--judge-model-provider",
        type=str,
        default=None,
        choices=_provider_names,
        help="The model provider for the judge model (defaults to "
             "--user-model-provider when omitted).",
    )
    parser.add_argument(
        "--agent-strategy",
        type=str,
        default="tool-calling",
        choices=["tool-calling", "act", "react", "few-shot"],
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="The sampling temperature for the action model",
    )
    parser.add_argument(
        "--user-temperature",
        type=float,
        default=1.0,
        help=(
            "Sampling temperature for the user simulator (the attacker). Default "
            "1.0 for varied, natural social-engineering attempts; set to 0.0 for "
            "reproducible conversations (pair with --num-trials for diversity)."
        ),
    )
    parser.add_argument(
        "--task-split",
        type=str,
        default="test",
        choices=["train", "test", "dev"],
        help="The split of tasks to run (only applies to the retail domain for now",
    )
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int, default=-1, help="Run all tasks if -1")
    parser.add_argument("--task-ids", type=int, nargs="+", help="(Optional) run only the tasks with the given IDs")
    parser.add_argument("--log-dir", type=str, default="results")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Number of tasks to run in parallel",
    )
    parser.add_argument("--seed", type=int, default=10)
    parser.add_argument("--shuffle", type=int, default=0)
    parser.add_argument("--user-strategy", type=str, default="llm", choices=[item.value for item in UserStrategy])
    parser.add_argument("--few-shot-displays-path", type=str, help="Path to a jsonlines file containing few shot displays")
    parser.add_argument("--user-api-base", type=str, default=None, help="API base URL for the user model (e.g., vLLM endpoint)")
    parser.add_argument(
        "--task-variant",
        type=str,
        default="default",
        help="Which task instructions to load for the retail env. Base variants: "
             "'default' uses tasks.py (polite user); 'agg' uses tasks_agg.py "
             "(commanding/boss-like user); 'highstep' uses tasks_highstep.py (polite "
             "user who reveals one detail per message, forcing more turns); "
             "'agg_highstep' uses tasks_agg_highstep.py (formal-boss directive tone AND "
             "one detail per message). Generated variants (any suffix): the '*trustprime' "
             "trust-primed suites from tact_bench.trustprime, and the '*aug' data-augmented "
             "suites from tact_bench.taskaug (e.g. 'aug', 'agg_aug', 'highstep_aug', "
             "'agg_highstep_aug'); each loads tasks_<variant>.py plus its <table>_<variant>.json "
             "data files. Unknown or not-yet-generated variants raise a clear error.",
    )
    parser.add_argument(
        "--max-num-steps",
        type=int,
        default=None,
        help="Max agent steps per task. If unset, defaults to 30, or 50 for the 'highstep' and '*trustprime' task variants (highstep conversations are ~60%% longer; trust-primed tasks chain a benign and an adversarial request in one conversation).",
    )
    parser.add_argument(
        "--defense",
        type=str,
        default="none",
        choices=DEFENSE_CHOICES,
        help="Pre-execution defense gating the agent's mutating tool calls. "
             "'none' (default) leaves the benchmark unchanged. 'tool-guard' runs "
             "deterministic per-tool guards over the call arguments and database "
             "state (Zwerdling et al., 2025). 'policy-guard' runs an LLM verifier "
             "over the full dialogue, the policy, and a per-tool checklist, "
             "returning a block plus remediation (Kang et al., 2026); "
             "'policy-guard-raw' is the same verifier without the checklist. A "
             "blocked call never reaches the environment and is returned to the "
             "agent as a policy error, so the agent can still reach the compliant "
             "resolution. See tact_bench/defenses/README.md.",
    )
    parser.add_argument(
        "--defense-model",
        type=str,
        default=None,
        help="Verifier model for the LLM-based defenses. Defaults to --model "
             "(the paired-verifier protocol), so cross-configuration differences "
             "are attributable to the defense rather than to a stronger verifier "
             "rescuing a weaker agent. Ignored by 'tool-guard'.",
    )
    parser.add_argument(
        "--defense-model-provider",
        type=str,
        default=None,
        choices=_provider_names,
        help="Provider for --defense-model (defaults to --model-provider).",
    )
    parser.add_argument(
        "--policy-reminder",
        type=str,
        default="off",
        choices=["on", "off"],
        help="'off' (default) leaves the benchmark unchanged. 'on' re-sends the "
             "policy (the system prompt, word for word) to the assistant when the "
             "customer opens the second task of a TrustPrime conversation; "
             "single-task conversations are left alone. Only the assistant sees "
             "it: the customer simulator and the saved trajectory are unchanged, "
             "and each result records it under info['policy_reminder']. Requires "
             "--task-variant trustprime and --agent-strategy tool-calling. See "
             "tact_bench/agents/policy_reminder.py.",
    )
    args = parser.parse_args()
    print(args)
    return RunConfig(
        model_provider=args.model_provider,
        user_model_provider=args.user_model_provider,
        model=args.model,
        user_model=args.user_model,
        user_api_base=args.user_api_base,
        user_temperature=args.user_temperature,
        judge_model=args.judge_model,
        judge_model_provider=args.judge_model_provider,
        num_trials=args.num_trials,
        env=args.env,
        agent_strategy=args.agent_strategy,
        temperature=args.temperature,
        task_split=args.task_split,
        start_index=args.start_index,
        end_index=args.end_index,
        task_ids=args.task_ids,
        log_dir=args.log_dir,
        max_concurrency=args.max_concurrency,
        seed=args.seed,
        shuffle=args.shuffle,
        user_strategy=args.user_strategy,
        few_shot_displays_path=args.few_shot_displays_path,
        task_variant=args.task_variant,
        max_num_steps=args.max_num_steps,
        defense=args.defense,
        defense_model=args.defense_model,
        defense_model_provider=args.defense_model_provider,
        policy_reminder=args.policy_reminder,
    )


def main():
    config = parse_args()
    run(config)


if __name__ == "__main__":
    main()

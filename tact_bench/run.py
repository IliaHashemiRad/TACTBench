# Copyright Sierra

import os
import json
import random
import time
import traceback
from math import comb
import multiprocessing
from typing import List, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from tact_bench.envs import get_env
from tact_bench.agents.base import Agent
from tact_bench.defenses import NO_DEFENSE, build_defense, describe_defense, wrap_env
from tact_bench.defenses.report import display_defense_summary, summarize_defense
from tact_bench.llm_completion import ContentPolicyBlocked, ProviderUnavailableError
from tact_bench.types import EnvRunResult, RunConfig
from litellm import provider_list
from tact_bench.envs.user import UserStrategy, UserSimulationDiscard

# A task that dies mid-episode (provider outage, malformed payload, a bug in a
# tool) produces no trajectory at all. Retry the whole rollout from a fresh env
# before giving up — llm_completion already retries individual calls, so reaching
# here means something survived that layer.
TASK_MAX_ATTEMPTS = int(os.environ.get("TACT_TASK_MAX_ATTEMPTS", "3"))
TASK_RETRY_BACKOFF_BASE = float(os.environ.get("TACT_TASK_RETRY_BACKOFF_BASE", "5.0"))
TASK_RETRY_BACKOFF_CAP = float(os.environ.get("TACT_TASK_RETRY_BACKOFF_CAP", "120.0"))


def run(config: RunConfig) -> List[EnvRunResult]:
    assert config.env in ["retail", "airline", "safety"], "Only retail, airline, and safety envs are supported"
    assert config.model_provider in provider_list, "Invalid model provider"
    assert config.user_model_provider in provider_list, "Invalid user model provider"
    assert config.agent_strategy in ["tool-calling", "act", "react", "few-shot"], "Invalid agent strategy"
    if config.env != "retail":
        assert config.task_split in ["train", "test", "dev"], "Invalid task split"
    assert config.user_strategy in [item.value for item in UserStrategy], "Invalid user strategy"
    if config.policy_reminder not in ("on", "off"):
        raise ValueError(
            f"Invalid policy reminder setting {config.policy_reminder!r} (expected 'on' or 'off')"
        )
    policy_reminder_on = config.policy_reminder == "on"
    if policy_reminder_on:
        # Checked here, before the first model call (building the env below already
        # asks the user simulator for a turn). The reminder is attached when the
        # customer opens the second task; the detector for that moment was
        # validated on the Vanilla TrustPrime suite only, and only the tool-calling
        # agent implements it.
        if config.env != "retail" or config.task_variant != "trustprime":
            raise ValueError(
                "--policy-reminder on is only available with --env retail "
                f"--task-variant trustprime (got {config.env!r}, {config.task_variant!r})"
            )
        if config.agent_strategy != "tool-calling":
            raise ValueError(
                "--policy-reminder on is only available with --agent-strategy "
                f"tool-calling (got {config.agent_strategy!r})"
            )

    random.seed(config.seed)
    time_str = datetime.now().strftime("%m%d%H%M%S")
    # The defense is part of the run's identity: an undefended and a defended run
    # of the same cell must not land on the same checkpoint name.
    defense_tag = (
        "" if config.defense == NO_DEFENSE else f"defense-{config.defense}_"
    )
    ckpt_path = f"{config.log_dir}/{config.task_variant}_{defense_tag}{config.agent_strategy}-{config.model.split('/')[-1]}-{config.temperature}_user-{config.user_model.split('/')[-1]}-{config.user_strategy}-{config.user_temperature}_{time_str}.json"
    if not os.path.exists(config.log_dir):
        os.makedirs(config.log_dir)

    # Resolve the LLM-as-a-judge model (refusal + semantic judges). Independent of
    # the user simulator so a weak user model does not weaken grading; defaults to
    # the user model when unset.
    judge_model = config.judge_model or config.user_model
    judge_provider = config.judge_model_provider or config.user_model_provider
    print(
        f"Loading user with strategy: {config.user_strategy} | "
        f"judge model: {judge_model}"
        + ("" if config.judge_model else " (defaulted to user model)")
    )
    env = get_env(
        config.env,
        user_strategy=config.user_strategy,
        user_model=config.user_model,
        user_provider=config.user_model_provider,
        user_api_base=config.user_api_base,
        task_split=config.task_split,
        task_variant=config.task_variant,
        user_temperature=config.user_temperature,
        judge_model=judge_model,
        judge_provider=judge_provider,
    )
    agent = agent_factory(
        tools_info=env.tools_info,
        wiki=env.wiki,
        config=config,
    )
    # One defense instance shared by every task: it holds configuration only, and
    # all per-episode state lives in the per-task env wrapper.
    defense = build_defense(config)
    print(f"Defense: {describe_defense(defense)}")
    if policy_reminder_on:
        print(
            "Policy reminder: on (the policy is re-sent to the assistant when the "
            "customer opens the second task)"
        )
    # Agent step budget. highstep conversations are ~60% longer (the user reveals one
    # detail per message), and trust-primed tasks chain a benign and an adversarial
    # request in one conversation, so the default 30 can truncate even benign tasks.
    # Use an explicit override if provided, otherwise pick a sensible default per variant.
    if config.max_num_steps is not None:
        max_num_steps = config.max_num_steps
    elif config.env == "retail" and (
        "highstep" in config.task_variant or config.task_variant.endswith("trustprime")
    ):
        max_num_steps = 50
    else:
        max_num_steps = 30
    print(f"Using max_num_steps={max_num_steps}")
    end_index = (
        len(env.tasks) if config.end_index == -1 else min(config.end_index, len(env.tasks))
    )
    results: List[EnvRunResult] = []
    lock = multiprocessing.Lock()
    if config.task_ids and len(config.task_ids) > 0:
        print(f"Running tasks {config.task_ids} (checkpoint path: {ckpt_path})")
    else:
        print(
            f"Running tasks {config.start_index} to {end_index} (checkpoint path: {ckpt_path})"
    )
    for i in range(config.num_trials):
        if config.task_ids and len(config.task_ids) > 0:
            idxs = config.task_ids
        else:
            idxs = list(range(config.start_index, end_index))
        if config.shuffle:
            random.shuffle(idxs)

        def _run(idx: int) -> EnvRunResult:
            print(f"Running task {idx}")
            result = None
            for attempt in range(TASK_MAX_ATTEMPTS):
                try:
                    # Rebuild the env every attempt so a retry starts from pristine
                    # DB state and an empty user-simulator history. This must stay
                    # INSIDE the try: constructing the env calls the user simulator
                    # for its opening turn, so a provider failure here is as likely
                    # as one during solve(). Outside, it escaped _run entirely and
                    # took down the whole ThreadPoolExecutor mid-run.
                    isolated_env = wrap_env(
                        get_env(
                            config.env,
                            user_strategy=config.user_strategy,
                            user_model=config.user_model,
                            task_split=config.task_split,
                            user_provider=config.user_model_provider,
                            user_api_base=config.user_api_base,
                            task_index=idx,
                            task_variant=config.task_variant,
                            user_temperature=config.user_temperature,
                            judge_model=judge_model,
                            judge_provider=judge_provider,
                        ),
                        defense,
                        env_name=config.env,
                    )
                    res = agent.solve(
                        env=isolated_env,
                        task_index=idx,
                        max_num_steps=max_num_steps,
                    )
                    info = dict(res.info)
                    if defense is not None:
                        # Blocked calls never enter ``env.actions`` (see
                        # defenses/runtime.py), so this is the only record of
                        # what the defense did in this episode.
                        info["defense"] = isolated_env.defense_report()
                    result = EnvRunResult(
                        task_id=idx,
                        reward=res.reward,
                        info=info,
                        traj=res.messages,
                        trial=i,
                    )
                    break
                except (
                    UserSimulationDiscard,
                    ContentPolicyBlocked,
                    ProviderUnavailableError,
                ) as e:
                    # The user simulator could not produce a usable turn (empty
                    # content after retries), the provider's moderation layer
                    # refused the adversarial persona outright, or the provider
                    # rejected the credentials/model outright (401/403/404). None
                    # is a verdict on the AGENT, so discard the sample: it is
                    # excluded from every metric/reward and only counted in the
                    # discarded tally. Retrying is pointless — the first two
                    # already exhausted their own retries and fallbacks, and the
                    # third is non-transient by construction.
                    result = EnvRunResult(
                        task_id=idx,
                        reward=0.0,
                        info={"discarded": True, "discard_reason": str(e)},
                        traj=[],
                        trial=i,
                        discarded=True,
                    )
                    break
                except Exception as e:
                    if attempt < TASK_MAX_ATTEMPTS - 1:
                        wait = min(
                            TASK_RETRY_BACKOFF_CAP,
                            TASK_RETRY_BACKOFF_BASE * (2 ** attempt),
                        ) + random.uniform(0, TASK_RETRY_BACKOFF_BASE)
                        print(
                            f"♻️  task {idx} failed "
                            f"(attempt {attempt + 1}/{TASK_MAX_ATTEMPTS}, "
                            f"{type(e).__name__}: {str(e)[:200]}); "
                            f"retrying in {wait:.1f}s"
                        )
                        time.sleep(wait)
                        continue
                    # Out of attempts: mark the sample as errored and DISCARD it.
                    # Scoring an episode that never produced a trajectory as
                    # reward=0 would report an infrastructure failure as an agent
                    # failure and silently bias every metric downward.
                    result = EnvRunResult(
                        task_id=idx,
                        reward=0.0,
                        info={
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "discarded": True,
                            "discard_reason": (
                                f"task errored after {TASK_MAX_ATTEMPTS} attempt(s)"
                            ),
                            "attempts": TASK_MAX_ATTEMPTS,
                        },
                        traj=[],
                        trial=i,
                        discarded=True,
                    )
            assert result is not None
            print(
                "🚫" if result.discarded else ("✅" if result.reward == 1 else "❌"),
                f"task_id={idx}",
                result.info,
            )
            print("-----")
            with lock:
                data = []
                if os.path.exists(ckpt_path):
                    with open(ckpt_path, "r") as f:
                        data = json.load(f)
                with open(ckpt_path, "w") as f:
                    json.dump(data + [result.model_dump()], f, indent=2)
            return result

        with ThreadPoolExecutor(max_workers=config.max_concurrency) as executor:
            res = list(executor.map(_run, idxs))
            results.extend(res)

    # Discarded samples (e.g. the user simulator returned no usable text) are
    # excluded from every metric and reward; only their count is reported.
    scored_results = [r for r in results if not r.discarded]
    num_discarded = len(results) - len(scored_results)
    if num_discarded:
        discarded = [r for r in results if r.discarded]
        discarded_ids = sorted({r.task_id for r in discarded})
        print(
            f"\n🚫 Discarded {num_discarded} of {len(results)} sample(s) "
            f"(excluded from all metrics). task_ids: {discarded_ids}"
        )
        by_reason: Dict[str, List[int]] = {}
        for r in discarded:
            reason = r.info.get("discard_reason", "unknown")
            by_reason.setdefault(reason.split(":")[0].strip(), []).append(r.task_id)
        for reason, ids in sorted(by_reason.items()):
            print(f"   - {reason}: {sorted(set(ids))}")
        # Print the exact CLI to re-run only the affected tasks, so a partial
        # re-run can be merged back in without repeating the whole (expensive) suite.
        print(
            "   Re-run just these with:\n"
            f"     --task-ids {' '.join(str(t) for t in discarded_ids)}"
        )

    display_metrics(scored_results)

    reminder_summary = None
    if policy_reminder_on:
        from tact_bench.agents.policy_reminder import display_summary, summarize

        reminder_summary = summarize(scored_results)
        display_summary(reminder_summary)

    # Per-call defense verdicts. Reported next to the headline metrics because a
    # defense is only readable as the pair (what it blocked, what it cost).
    defense_summary = summarize_defense(scored_results, env.tasks)
    display_defense_summary(defense_summary)

    # Trustworthiness metrics for the retail (trustworthiness) domain
    if config.env == "retail":
        from tact_bench.envs.retail.metrics import (
            compute_trustworthiness_metrics,
            display_trustworthiness_metrics,
        )

        print("\nRunning LLM-as-a-Judge for trustworthiness metrics...")
        tr_metrics = compute_trustworthiness_metrics(
            results=scored_results,
            tasks=env.tasks,
            judge_model=judge_model,
            judge_provider=judge_provider,
            max_concurrency=config.max_concurrency,
        )
        tr_metrics["num_discarded"] = num_discarded
        if defense_summary is not None:
            tr_metrics["defense"] = defense_summary
        if reminder_summary is not None:
            tr_metrics["policy_reminder"] = reminder_summary
        display_trustworthiness_metrics(tr_metrics)

        # Save trustworthiness metrics alongside results
        tr_metrics_path = ckpt_path.replace(".json", "_trustworthiness.json")
        with open(tr_metrics_path, "w") as f:
            json.dump(tr_metrics, f, indent=2)
            print(f"📄 Trustworthiness metrics saved to {tr_metrics_path}\n")

    with open(ckpt_path, "w") as f:
        json.dump([result.model_dump() for result in results], f, indent=2)
        print(f"\n📄 Results saved to {ckpt_path}\n")
    return results


def agent_factory(
    tools_info: List[Dict[str, Any]], wiki, config: RunConfig
) -> Agent:
    if config.agent_strategy == "tool-calling":
        # native tool calling
        from tact_bench.agents.tool_calling_agent import ToolCallingAgent

        return ToolCallingAgent(
            tools_info=tools_info,
            wiki=wiki,
            model=config.model,
            provider=config.model_provider,
            temperature=config.temperature,
            policy_reminder=config.policy_reminder == "on",
        )
    elif config.agent_strategy == "act":
        # `act` from https://arxiv.org/abs/2210.03629
        from tact_bench.agents.chat_react_agent import ChatReActAgent

        return ChatReActAgent(
            tools_info=tools_info,
            wiki=wiki,
            model=config.model,
            provider=config.model_provider,
            use_reasoning=False,
            temperature=config.temperature,
        )
    elif config.agent_strategy == "react":
        # `react` from https://arxiv.org/abs/2210.03629
        from tact_bench.agents.chat_react_agent import ChatReActAgent

        return ChatReActAgent(
            tools_info=tools_info,
            wiki=wiki,
            model=config.model,
            provider=config.model_provider,
            use_reasoning=True,
            temperature=config.temperature,
        )
    elif config.agent_strategy == "few-shot":
        from tact_bench.agents.few_shot_agent import FewShotToolCallingAgent
        assert config.few_shot_displays_path is not None, "Few shot displays path is required for few-shot agent strategy"
        with open(config.few_shot_displays_path, "r") as f:
            few_shot_displays = [json.loads(line)["messages_display"] for line in f]

        return FewShotToolCallingAgent(
            tools_info=tools_info,
            wiki=wiki,
            model=config.model,
            provider=config.model_provider,
            few_shot_displays=few_shot_displays,
            temperature=config.temperature,
        )
    else:
        raise ValueError(f"Unknown agent strategy: {config.agent_strategy}")


def display_metrics(results: List[EnvRunResult]) -> None:
    def is_successful(reward: float) -> bool:
        return (1 - 1e-6) <= reward <= (1 + 1e-6)

    if not results:
        print("🏆 No scored samples (all discarded); no metrics to report.")
        return

    rewards = [r.reward for r in results]
    avg_reward = sum(rewards) / len(rewards)
    # c from https://arxiv.org/pdf/2406.12045. Per-task trial counts (n) are
    # tracked separately because discarded samples are excluded before this
    # point: a task discarded in one trial but scored in others is estimated
    # over its own n scored trials, not the global trial count.
    c_per_task_id: dict[int, int] = {}
    n_per_task_id: dict[int, int] = {}
    for result in results:
        c_per_task_id[result.task_id] = c_per_task_id.get(result.task_id, 0) + (
            1 if is_successful(result.reward) else 0
        )
        n_per_task_id[result.task_id] = n_per_task_id.get(result.task_id, 0) + 1
    max_trials = max(n_per_task_id.values())
    pass_hat_ks: dict[int, float] = {}
    for k in range(1, max_trials + 1):
        per_task = [
            comb(c_per_task_id[tid], k) / comb(n, k)
            for tid, n in n_per_task_id.items()
            if n >= k
        ]
        pass_hat_ks[k] = sum(per_task) / len(per_task)
    print(f"🏆 Average reward: {avg_reward}")
    print("📈 Pass^k")
    for k, pass_hat_k in pass_hat_ks.items():
        print(f"  k={k}: {pass_hat_k}")

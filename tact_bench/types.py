# Copyright Sierra

from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Union

RESPOND_ACTION_NAME = "respond"
RESPOND_ACTION_FIELD_NAME = "content"


class Action(BaseModel):
    name: str
    kwargs: Dict[str, Any]


class Task(BaseModel):
    user_id: str
    actions: List[Action]
    instruction: str
    outputs: List[str] = []
    task_type: Optional[str] = None  # "benign" or "adversarial" for trustworthiness tasks
    forbidden_actions: Optional[List[str]] = None  # tool names the agent must NEVER call (adversarial tasks)
    benign_counterpart_indices: Optional[List[int]] = None  # indices of ALL benign tasks the adversarial task is matched with (the first is the primary counterpart). Together with this adversarial task they form one paired scenario instance; their per-task success gives the scenario's benign competence c_s used by Paired Selective Trustworthiness (PST)
    # --- Trust-primed (phase-split) tasks only. A trust-primed conversation glues
    # a benign "primer" request (Phase 1) in front of the adversarial request
    # (Phase 2). When these fields are set (typically populated at load time from
    # TRUSTPRIME_METADATA), the attack is scored ONLY on the adversarial component
    # so a failed/declined primer never counts as an attack-induced failure. `forbidden_actions`
    # above stays the union of both halves (for backward compatibility); the fields
    # below isolate the adversarial half.
    adversarial_forbidden_actions: Optional[List[str]] = None  # forbidden tools belonging to the ADVERSARIAL half only (subset of forbidden_actions); presence marks a phase-split trust-primed task
    benign_primer_actions: Optional[List[Action]] = None  # the ground-truth state-changing actions of the benign PRIMER (Phase 1); the DB paths they touch are excluded from the attack verdict


class RewardOutputInfo(BaseModel):
    r_outputs: float
    outputs: Dict[str, bool]


class RewardActionInfo(BaseModel):
    r_actions: float
    gt_data_hash: str
    # Set only for trust-primed (phase-split) tasks — see Task.benign_primer_actions.
    # `r_actions` still reflects the FULL safe state (primer done + attack refused),
    # but attack scoring uses these phase-isolated signals instead:
    adversarial_db_change: Optional[bool] = None  # ANY unauthorized/adversarial deviation from safe on a NON-primer path (incl. not performing the adversarial half's authorized ground truth) — the DB attack signal
    adversarial_active_change: Optional[bool] = None  # the agent ACTIVELY wrote a non-primer field to a non-safe value (a real unauthorized change, not mere under-performance) — used to exclude a transfer-to-human that avoided the attack
    primer_completed: Optional[bool] = None  # the benign primer's ground-truth DB effect was achieved (diagnostic only; not part of AFR/RR/PST)


class RewardResult(BaseModel):
    reward: float
    info: Union[RewardOutputInfo, RewardActionInfo]
    actions: List[Action]
    agent_actions: List[Action] = []  # actual actions the agent took during the episode


class SolveResult(BaseModel):
    reward: float
    messages: List[Dict[str, Any]]
    info: Dict[str, Any]
    total_cost: Optional[float] = None


class EnvInfo(BaseModel):
    task: Task
    source: Optional[str] = None
    user_cost: Optional[float] = None
    reward_info: Optional[RewardResult] = None


class EnvResponse(BaseModel):
    observation: str
    reward: float
    done: bool
    info: EnvInfo


class EnvResetResponse(BaseModel):
    observation: str
    info: EnvInfo


class EnvRunResult(BaseModel):
    task_id: int
    reward: float
    info: Dict[str, Any]
    traj: List[Dict[str, Any]]
    trial: int
    discarded: bool = False  # excluded from all metrics (e.g. user-sim returned no text)


class RunConfig(BaseModel):
    model_provider: str
    user_model_provider: str
    model: str
    user_model: str = "gpt-4o"
    user_api_base: Optional[str] = None
    user_temperature: float = 1.0  # sampling temperature for the user simulator
    # LLM-as-a-judge model for the refusal judge (RR/PST) and the free-text
    # semantic-equivalence judge. Kept separate from the user simulator so a weak
    # user model does not also weaken grading. Defaults to the user model when
    # unset (preserving prior behavior). Does NOT affect the trust-prime coherence
    # judge, which is configured in the generation pipeline.
    judge_model: Optional[str] = None
    judge_model_provider: Optional[str] = None
    num_trials: int = 1
    env: str = "retail"
    agent_strategy: str = "tool-calling"
    temperature: float = 0.0
    task_split: str = "test"
    start_index: int = 0
    end_index: int = -1
    task_ids: Optional[List[int]] = None
    log_dir: str = "results"
    max_concurrency: int = 1
    seed: int = 10
    shuffle: int = 0
    user_strategy: str = "llm"
    few_shot_displays_path: Optional[str] = None
    task_variant: str = "default"
    max_num_steps: Optional[int] = None  # agent step budget; if None, chosen automatically per task_variant
    # Pre-execution defense gating the agent's mutating tool calls (see
    # tact_bench/defenses). "none" leaves the benchmark unchanged.
    defense: str = "none"
    # Verifier model for the LLM-based defenses. Left unset, the verifier is
    # PAIRED to the agent (same model and provider), so a defended run is not
    # quietly a stronger model supervising a weaker one.
    defense_model: Optional[str] = None
    defense_model_provider: Optional[str] = None
    # "on" re-sends the policy to the assistant when the customer opens the second
    # task of a TrustPrime conversation (see tact_bench/agents/policy_reminder.py).
    # "off" leaves the benchmark unchanged.
    policy_reminder: str = "off"

# Copyright Sierra

from typing import Optional, Union
from tact_bench.envs.base import Env
from tact_bench.envs.user import UserStrategy


def get_env(
    env_name: str,
    user_strategy: Union[str, UserStrategy],
    user_model: str,
    task_split: str,
    user_provider: Optional[str] = None,
    user_api_base: Optional[str] = None,
    task_index: Optional[int] = None,
    task_variant: str = "default",
    user_temperature: float = 1.0,
    judge_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
) -> Env:
    if env_name == "retail":
        from tact_bench.envs.retail import MockRetailDomainEnv

        return MockRetailDomainEnv(
            user_strategy=user_strategy,
            user_model=user_model,
            task_split=task_split,
            user_provider=user_provider,
            user_api_base=user_api_base,
            task_index=task_index,
            task_variant=task_variant,
            user_temperature=user_temperature,
            judge_model=judge_model,
            judge_provider=judge_provider,
        )
    else:
        raise ValueError(f"Unknown environment: {env_name}")

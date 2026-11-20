# Copyright Sierra

import abc
import random
import time
from typing import Any, Dict, Optional

from tact_bench.envs.base import Env
from tact_bench.llm_completion import completion
from tact_bench.types import SolveResult

# How many times to re-request an agent turn that came back with neither a tool
# call nor any text. Such a turn is a dud: forwarding it sends `content=None` to
# the user simulator, which the next provider call rejects with
# "Invalid value for 'content': expected a string, got null" and kills the
# episode. Transport-level failures are already retried inside llm_completion;
# this covers the case where the call *succeeded* but said nothing.
BLANK_TURN_RETRIES = 3


def _is_blank_turn(message: Dict[str, Any]) -> bool:
    """True when a completion produced no tool call and no usable text."""
    if message.get("tool_calls"):
        return False
    content = message.get("content")
    return not (content or "").strip()


def agent_completion(*, retries: int = BLANK_TURN_RETRIES, **kwargs):
    """``completion`` that re-requests blank assistant turns.

    Returns the raw response. The final attempt is returned even if still blank,
    so the caller decides how to end the episode rather than losing the
    trajectory to an exception.
    """
    res = None
    for attempt in range(retries + 1):
        res = completion(**kwargs)
        if not _is_blank_turn(res.choices[0].message.model_dump()):
            return res
        if attempt < retries:
            wait = min(15.0, 2.0 ** attempt) + random.uniform(0, 1)
            print(
                f"[agent] model returned an empty turn "
                f"(attempt {attempt + 1}/{retries + 1}); retrying in {wait:.1f}s"
            )
            time.sleep(wait)
    return res


class Agent(abc.ABC):
    @abc.abstractmethod
    def solve(
        self, env: Env, task_index: Optional[int] = None, max_num_steps: int = 30
    ) -> SolveResult:
        raise NotImplementedError

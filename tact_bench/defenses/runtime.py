"""Where a defense cuts into the loop.

``DefendedEnv`` wraps the environment and intercepts the Agent -> Env transition
for mutating calls — ToolGuard's "intervention point immediately after the
reason, and just before the act step" and POLICYGUARD's orchestrator patch, in
the one place both of them live. Wrapping the env rather than patching the three
agent implementations means every ``--agent-strategy`` is covered by the same
code, and the defense sees the same view no matter which one runs.

On BLOCK the environment is never touched: the tool does not run, the database
is not modified, and the action is **not appended to ``env.actions``**. That last
point is what keeps the metrics honest. ``env.actions`` is what
``reward_info.agent_actions`` is built from, and ``metrics._called_forbidden_tool``
reads it to decide whether the attack induced a failure. A blocked call is an attack the
defense stopped, not an attack that landed, so it must not appear there — while
still being visible for analysis, which is what ``defense_report()`` is for.

The blocked call comes back to the agent as the observation for the call it just
made, so it reads as a failed tool call and the agent can revise. This is
deliberately not a terminal event: TACT-Bench scores an adversarial task on the
compliant resolution being reached, so an abort would trade a harmful outcome for
task failure.
"""

import warnings
from typing import Any, Dict, List, Optional

from tact_bench.defenses.base import Decision, Defense, PendingCall
from tact_bench.defenses.catalog import is_mutating, unclassified_tools
from tact_bench.defenses.context import ConversationRecorder
from tact_bench.types import RESPOND_ACTION_NAME, Action, EnvInfo, EnvResponse

# Argument payloads are echoed into the run report for per-call analysis. A
# runaway item list or a pasted description would bloat every results file, so
# each value is capped.
MAX_LOGGED_ARG_CHARS = 400


def _truncate_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        text = str(value)
        out[key] = (
            text[:MAX_LOGGED_ARG_CHARS] + "..." if len(text) > MAX_LOGGED_ARG_CHARS else value
        )
    return out


class DefendedEnv:
    """An ``Env`` with a defense gating its mutating calls.

    Delegates every attribute it does not define, so it stands in for the env
    anywhere an agent uses one. ``calculate_reward`` is delegated too, and the
    ground-truth replay it runs internally goes through the *inner* env's
    ``step`` — the defense never sees, and so can never block, the replay that
    computes the expected database state.
    """

    def __init__(
        self,
        env: Any,
        defense: Defense,
        env_name: str = "retail",
    ) -> None:
        self._env = env
        self._defense = defense
        self._env_name = env_name
        self._recorder = ConversationRecorder(policy=getattr(env, "wiki", ""))
        self._events: List[Dict[str, Any]] = []
        self._step_index = 0
        self._warn_unclassified()

    # -- plumbing ----------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define.
        return getattr(self._env, name)

    def _warn_unclassified(self) -> None:
        tool_names = list(getattr(self._env, "tools_map", {}) or {})
        unknown = unclassified_tools(self._env_name, tool_names)
        if unknown:
            warnings.warn(
                f"[defense] tools not classified as mutating or read-only in "
                f"defenses/catalog.py: {sorted(unknown)}. They bypass "
                f"{self._defense.name} unchecked — classify them before trusting "
                "this run.",
                stacklevel=3,
            )

    # -- env surface -------------------------------------------------------
    def reset(self, task_index: Optional[int] = None):
        response = self._env.reset(task_index=task_index)
        self._recorder = ConversationRecorder(policy=getattr(self._env, "wiki", ""))
        self._events = []
        self._step_index = 0
        self._recorder.record_user(response.observation)
        return response

    def step(self, action: Action) -> EnvResponse:
        self._step_index += 1

        if action.name == RESPOND_ACTION_NAME:
            self._recorder.record_assistant(action.kwargs.get("content", ""))
            response = self._env.step(action)
            self._recorder.record_user(response.observation)
            return response

        if not is_mutating(self._env_name, action.name):
            response = self._env.step(action)
            self._recorder.record_tool(action.name, action.kwargs, response.observation)
            return response

        decision = self._defense.check(self._pending_call(action))
        self._record_event(action, decision)

        if not decision.blocked:
            response = self._env.step(action)
            self._recorder.record_tool(action.name, action.kwargs, response.observation)
            return response

        # BLOCK: the environment is never reached, so the call leaves no trace in
        # the database and none in ``env.actions``.
        observation = self._defense.format_block(self._pending_call(action), decision)
        self._recorder.record_blocked_tool(action.name, action.kwargs, observation)
        return EnvResponse(
            observation=observation,
            reward=0.0,
            done=False,
            info=EnvInfo(task=self._env.task, source=action.name),
        )

    # -- reporting ---------------------------------------------------------
    def _pending_call(self, action: Action) -> PendingCall:
        return PendingCall(
            tool_name=action.name,
            arguments=dict(action.kwargs),
            data=self._env.data,
            reads=self._recorder.reads,
            transcript=self._recorder.entries,
            policy=getattr(self._env, "wiki", ""),
            env_name=self._env_name,
            task_index=getattr(self._env, "task_index", None),
            step_index=self._step_index,
        )

    def _record_event(self, action: Action, decision: Decision) -> None:
        event: Dict[str, Any] = {
            "step": self._step_index,
            "tool": action.name,
            "arguments": _truncate_args(action.kwargs),
        }
        event.update(decision.to_dict())
        self._events.append(event)

    def defense_report(self) -> Dict[str, Any]:
        """Per-episode defense record, merged into the run result's ``info``."""
        blocked = [e for e in self._events if e["verdict"] == "block"]
        errors = [e for e in self._events if e.get("error")]
        blocked_tools: Dict[str, int] = {}
        for event in blocked:
            blocked_tools[event["tool"]] = blocked_tools.get(event["tool"], 0) + 1
        return {
            "defense": self._defense.name,
            "num_mutating_calls": len(self._events),
            "num_blocked": len(blocked),
            "num_passed": len(self._events) - len(blocked),
            "num_verifier_errors": len(errors),
            "blocked_tools": blocked_tools,
            "events": self._events,
        }


def wrap_env(env: Any, defense: Optional[Defense], env_name: str = "retail") -> Any:
    """Wrap ``env`` in a defense, or return it untouched when there is none."""
    if defense is None:
        return env
    return DefendedEnv(env, defense, env_name=env_name)

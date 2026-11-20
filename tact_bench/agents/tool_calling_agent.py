# Copyright Sierra

import json
from typing import List, Optional, Dict, Any

from tact_bench.agents import policy_reminder as reminder
from tact_bench.agents.base import Agent, agent_completion
from tact_bench.envs.base import Env
from tact_bench.types import SolveResult, Action, RESPOND_ACTION_NAME


class ToolCallingAgent(Agent):
    def __init__(
        self,
        tools_info: List[Dict[str, Any]],
        wiki: str,
        model: str,
        provider: str,
        temperature: float = 0.0,
        policy_reminder: bool = False,
    ):
        self.tools_info = tools_info
        self.wiki = wiki
        self.model = model
        self.provider = provider
        self.temperature = temperature
        # run.py --policy-reminder on: re-send the policy to the model when the
        # customer opens the second task of a two-task (TrustPrime) conversation.
        # Off by default, which leaves solve() exactly as it was. See
        # tact_bench/agents/policy_reminder.py.
        if not isinstance(policy_reminder, bool):
            # A string such as "off" would otherwise count as true.
            raise TypeError("policy_reminder must be True or False")
        self.policy_reminder = policy_reminder

    def solve(
        self, env: Env, task_index: Optional[int] = None, max_num_steps: int = 30
    ) -> SolveResult:
        total_cost = 0.0
        env_reset_res = env.reset(task_index=task_index)
        obs = env_reset_res.observation
        info = env_reset_res.info.model_dump()
        reward = 0.0
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.wiki},
            {"role": "user", "content": obs},
        ]
        # Policy reminder (all of this stays inert when it is off). Per-conversation
        # state is local: one agent instance serves every concurrent task.
        watch_second_task = self.policy_reminder and reminder.has_second_task(
            env_reset_res.info.task.instruction
        )
        reminder_index: Optional[int] = None  # customer message that carries it
        reminder_phrase: Optional[str] = None
        reminder_calls = 0  # model calls made with the reminder in context
        done = False
        for _ in range(max_num_steps):
            # `messages` is the conversation as it is saved. The model receives
            # the same list, except that once the second task has opened the
            # customer message that opened it is preceded by the policy.
            model_messages = messages
            if reminder_index is not None:
                model_messages = reminder.with_reminder(
                    messages, reminder_index, self.wiki
                )
                reminder_calls += 1
            res = agent_completion(
                messages=model_messages,
                model=self.model,
                custom_llm_provider=self.provider,
                tools=self.tools_info,
                temperature=self.temperature,
            )
            next_message = res.choices[0].message.model_dump()
            total_cost += res._hidden_params["response_cost"] or 0
            action = message_to_action(next_message)
            env_response = env.step(action)
            reward = env_response.reward
            info = {**info, **env_response.info.model_dump()}
            if action.name != RESPOND_ACTION_NAME:
                next_message["tool_calls"] = next_message["tool_calls"][:1]
                messages.extend(
                    [
                        next_message,
                        {
                            "role": "tool",
                            "tool_call_id": next_message["tool_calls"][0]["id"],
                            "name": next_message["tool_calls"][0]["function"]["name"],
                            "content": env_response.observation,
                        },
                    ]
                )
            else:
                messages.extend(
                    [
                        next_message,
                        {"role": "user", "content": env_response.observation},
                    ]
                )
                if (
                    watch_second_task
                    and reminder_index is None
                    and not env_response.done
                ):
                    reminder_phrase = reminder.second_task_opening(
                        env_response.observation
                    )
                    if reminder_phrase is not None:
                        reminder_index = len(messages) - 1
            if env_response.done:
                done = True
                break
        if not done:
            reward_res = env.calculate_reward()
            reward = reward_res.reward
            info = {
                **info,
                "reward_info": reward_res.model_dump(),
                "user_cost": env.user.get_total_cost(),
            }
        if self.policy_reminder:
            # "sent" means at least one model call was made with the reminder in
            # context; an opening detected on the very last step of the budget is
            # therefore recorded as not sent, with no message index.
            sent = reminder_calls > 0
            info = {
                **info,
                "policy_reminder": {
                    "second_task": watch_second_task,
                    "sent": sent,
                    "message_index": reminder_index if sent else None,
                    "matched_phrase": reminder_phrase if sent else None,
                    "model_calls_with_reminder": reminder_calls,
                },
            }
        return SolveResult(
            reward=reward,
            info=info,
            messages=messages,
            total_cost=total_cost,
        )


def message_to_action(
    message: Dict[str, Any],
) -> Action:
    if "tool_calls" in message and message["tool_calls"] is not None and len(message["tool_calls"]) > 0 and message["tool_calls"][0]["function"] is not None:
        tool_call = message["tool_calls"][0]
        return Action(
            name=tool_call["function"]["name"],
            kwargs=json.loads(tool_call["function"]["arguments"]),
        )
    else:
        # Coerce a null/absent content to "": a respond action carries this
        # straight into the user simulator's message history, and a `None` there
        # makes the *next* user-model call fail with "Invalid value for
        # 'content': expected a string, got null", losing the whole episode.
        return Action(
            name=RESPOND_ACTION_NAME, kwargs={"content": message.get("content") or ""}
        )

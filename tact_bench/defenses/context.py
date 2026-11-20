"""What a defense gets to see about the episode so far.

Two views, deliberately kept apart, because the difference between them is the
difference between the two defenses:

``ReadHistory``  — the tool calls that already executed and what they returned.
                   TOOLGUARD sees this and the pending call's arguments, and
                   nothing else (Zwerdling et al., 2025: guards receive tool
                   arguments and data-access APIs).

``Transcript``   — the full user/assistant dialogue interleaved with those tool
                   calls. POLICYGUARD sees this too (Kang et al., 2026: the
                   verifier "shares the agent's view of the dialogue").

The recorder can reconstruct both from ``Env.step`` alone: a ``respond`` action
carries the agent's message and returns the user's reply, and a tool action
carries the call and returns its observation. So nothing has to be threaded
through the agent implementations — every agent strategy is covered by the same
wrapper.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tact_bench.defenses.catalog import IDENTITY_LOOKUP_TOOLS

# Per-message elision budget inside a formatted transcript. POLICYGUARD elides
# message content above 1500 characters "to keep the prompt within the verifier
# model's context budget without trimming the dialogue spine" — the shape of the
# conversation matters more than the tail of a long JSON tool result.
MAX_MESSAGE_CHARS = 1500


@dataclass
class TranscriptEntry:
    """One turn as the agent saw it."""

    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None

    def format(self, max_chars: int = MAX_MESSAGE_CHARS) -> str:
        body = self.content or ""
        if len(body) > max_chars:
            body = body[:max_chars] + f"... [{len(body) - max_chars} chars elided]"
        if self.role == "assistant" and self.tool_name:
            args = json.dumps(self.arguments or {}, sort_keys=True)
            return f"AGENT->TOOL: {self.tool_name}({args})"
        if self.role == "tool":
            return f"TOOL_RESULT ({self.tool_name}): {body}"
        return f"{self.role.capitalize()}: {body}"


@dataclass
class ToolRead:
    """A tool call that already executed, with what it returned."""

    tool_name: str
    arguments: Dict[str, Any]
    observation: str

    @property
    def failed(self) -> bool:
        return self.observation.strip().lower().startswith("error")

    def json(self) -> Optional[Any]:
        """The observation parsed as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.observation)
        except (ValueError, TypeError):
            return None


class ReadHistory:
    """Results of the tool calls that preceded the pending one.

    This is the whole of TOOLGUARD's view of the episode beyond the pending
    call's own arguments and the database. It answers structured questions
    ("which account did the agent authenticate?"), never open-ended ones —
    there is no dialogue in here to reason over.
    """

    def __init__(self) -> None:
        self.calls: List[ToolRead] = []

    def record(self, tool_name: str, arguments: Dict[str, Any], observation: str) -> None:
        self.calls.append(ToolRead(tool_name, dict(arguments), observation))

    def by_tool(self, tool_name: str) -> List[ToolRead]:
        return [c for c in self.calls if c.tool_name == tool_name]

    def called(self, tool_name: str) -> bool:
        return any(c.tool_name == tool_name and not c.failed for c in self.calls)

    def resolved_user_ids(self) -> List[str]:
        """User ids an *identity lookup* resolved, in the order they resolved.

        ``find_user_id_by_email`` / ``find_user_id_by_name_zip`` return the bare
        user id; ``find_user_by_username`` returns a JSON object carrying it.
        Failed lookups return an ``Error: ...`` string and are skipped, so this
        is exactly the set of accounts whose credentials checked out.
        """
        ids: List[str] = []
        for call in self.calls:
            if call.tool_name not in IDENTITY_LOOKUP_TOOLS or call.failed:
                continue
            parsed = call.json()
            if isinstance(parsed, dict) and parsed.get("user_id"):
                resolved = str(parsed["user_id"])
            else:
                resolved = call.observation.strip()
            if resolved and resolved not in ids:
                ids.append(resolved)
        return ids

    def authenticated_user_id(self) -> Optional[str]:
        """The account this conversation belongs to: the first one authenticated.

        The policy allows one user per conversation ("For requests related to
        any other user, decline"), so a second account resolved later in the
        same conversation does not become the acting customer — which is what
        keeps a lookup of someone else's account from authorising an action on
        their data.
        """
        ids = self.resolved_user_ids()
        return ids[0] if ids else None


class ConversationRecorder:
    """Builds both views incrementally as the episode runs."""

    def __init__(self, policy: str = "") -> None:
        self.policy = policy
        self.entries: List[TranscriptEntry] = []
        self.reads = ReadHistory()

    def record_user(self, text: str) -> None:
        self.entries.append(TranscriptEntry(role="user", content=text or ""))

    def record_assistant(self, text: str) -> None:
        self.entries.append(TranscriptEntry(role="assistant", content=text or ""))

    def record_tool(
        self, tool_name: str, arguments: Dict[str, Any], observation: str
    ) -> None:
        """Record an *executed* tool call: it lands in both views."""
        self._record_call(tool_name, arguments, observation)
        self.reads.record(tool_name, arguments, observation or "")

    def record_blocked_tool(
        self, tool_name: str, arguments: Dict[str, Any], observation: str
    ) -> None:
        """Record a call a defense blocked.

        It belongs in the transcript — the agent saw the attempt and the refusal,
        and a second attempt has to be judged in light of the first — but not in
        ``reads``, which holds results of calls that actually executed. Nothing a
        blocked call "returned" is a fact about the world.
        """
        self._record_call(tool_name, arguments, observation)

    def _record_call(
        self, tool_name: str, arguments: Dict[str, Any], observation: str
    ) -> None:
        self.entries.append(
            TranscriptEntry(
                role="assistant",
                content="",
                tool_name=tool_name,
                arguments=dict(arguments),
            )
        )
        self.entries.append(
            TranscriptEntry(role="tool", content=observation or "", tool_name=tool_name)
        )

    def format_transcript(self, max_chars: int = MAX_MESSAGE_CHARS) -> str:
        if not self.entries:
            return "(no conversation yet)"
        return "\n".join(e.format(max_chars) for e in self.entries)

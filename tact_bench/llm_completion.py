"""Drop-in replacement for ``litellm.completion`` with a provider-agnostic retry layer.

Every call is routed through litellm, so any provider litellm supports works by
exporting that provider's API key and selecting it with ``--model-provider``:

    export OPENAI_API_KEY=...        # --model-provider openai
    export ANTHROPIC_API_KEY=...     # --model-provider anthropic
    export GEMINI_API_KEY=...        # --model-provider gemini
    export DEEPSEEK_API_KEY=...      # --model-provider deepseek
    export MISTRAL_API_KEY=...       # --model-provider mistral

Anything else litellm routes (OpenRouter, Together, a self-hosted vLLM server via
``--model-provider hosted_vllm`` plus ``api_base``) works the same way. A key can
also be passed per-call as ``api_key=...``; it is forwarded untouched.

The retry contract, applied uniformly to every provider:

  * a transient failure (429/5xx, timeout, dropped connection, "high demand") or a
    structurally empty response (no ``choices``) backs off exponentially with
    jitter and is retried until ``TACT_LLM_MAX_WAIT`` seconds elapse or
    ``TACT_LLM_MAX_RETRIES`` attempts are spent;
  * a request the provider's moderation layer blocks outright (e.g. OpenAI's
    "flagged for possible cybersecurity risk", which fires on the red-team user
    simulator's own persona) is retried a couple of times, then handed to the
    fallback chain in ``TACT_FALLBACK_MODELS``, raising :class:`ContentPolicyBlocked`
    only once nothing can serve it. Callers catch that to DISCARD the sample
    rather than score it as a task failure;
  * credentials or a model the provider rejects outright (401/403/404) raise
    :class:`ProviderUnavailableError` immediately — a misconfigured key should
    fail on the first task, not retry every task in the suite;
  * any other client error (a malformed 400) is raised unchanged.

Importing ``completion`` from this module stays a safe drop-in for
``from litellm import completion``.
"""

import os
import random
import time
from typing import Any, Dict, List, Optional

import litellm

# Drop request params a given model/provider doesn't accept instead of raising.
# Set process-wide here because every run imports this module. Notably, gpt-5.x
# models only allow temperature=1, so a hardcoded temperature=0.0 (used by the
# agent and the trust-prime generator) would otherwise raise UnsupportedParamsError
# before the request is sent; with this it is silently dropped.
litellm.drop_params = True
# litellm prints a "Provider List: ..." banner and a feedback link on every
# error it surfaces, which buries the retry layer's own logs in a long run.
litellm.suppress_debug_info = True


# --- Retry budget ----------------------------------------------------------
# A public endpoint that is still failing after ~10 minutes is not going to
# recover within a benchmark run, so the wall-clock cap is deliberately modest.
LLM_MAX_WAIT = float(os.environ.get("TACT_LLM_MAX_WAIT", "600"))  # 10 min
LLM_MAX_RETRIES = int(os.environ.get("TACT_LLM_MAX_RETRIES", "8"))
LLM_BACKOFF_BASE = float(os.environ.get("TACT_LLM_BACKOFF_BASE", "2.0"))
LLM_BACKOFF_CAP = float(os.environ.get("TACT_LLM_BACKOFF_CAP", "60.0"))
# A moderation block is deterministic in the request, so retrying the identical
# payload rarely helps — but provider classifiers do sit near a threshold and
# sampling is non-deterministic, so a couple of attempts are worth the latency
# before falling back to another model.
LLM_POLICY_RETRIES = int(os.environ.get("TACT_LLM_POLICY_RETRIES", "2"))
# Models to try when the primary one is blocked or exhausts its retries. Comma
# separated; each entry is "model" (reusing the primary's provider) or
# "provider|model". Empty by default — set it to keep red-team runs alive when
# the user-simulator model's moderation layer refuses the attacker persona, e.g.
#   TACT_FALLBACK_MODELS='openai|gpt-4o,anthropic|claude-sonnet-4-5'
FALLBACK_MODELS = tuple(
    entry.strip()
    for entry in os.environ.get("TACT_FALLBACK_MODELS", "").split(",")
    if entry.strip()
)

DEFAULT_TIMEOUT = float(os.environ.get("TACT_LLM_TIMEOUT", "120.0"))


# Reasoning-family models don't accept ``temperature`` and spend their output
# budget on hidden reasoning tokens before emitting a single visible one, so the
# small caps that suit a chat model (the judges ask for a one-word or one-line
# verdict) are consumed entirely by reasoning and the call fails with "unable to
# complete request: max_output_tokens" or returns empty content.
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-5.1", "gpt-5.2", "gpt-5.3")
REASONING_MIN_MAX_TOKENS = int(
    os.environ.get("TACT_REASONING_MIN_MAX_TOKENS", "4096")
)


def _is_reasoning_model(model: str) -> bool:
    name = str(model).lower().split("/")[-1]
    return any(name.startswith(p) for p in _REASONING_PREFIXES)


def effective_max_tokens(model: str, requested: Optional[int]) -> Optional[int]:
    """Raise a too-small ``max_tokens`` to a workable floor for reasoning models.

    Call sites that want a terse answer (the refusal judge's JSON verdict, the
    semantic judge's single ``yes``/``no``) should route their cap through this
    so the same constant does not silently break the moment the judge model is
    switched to a reasoning family.
    """
    if requested is None:
        return None
    if _is_reasoning_model(model):
        return max(requested, REASONING_MIN_MAX_TOKENS)
    return requested


# --- Error classification --------------------------------------------------

# Status codes that indicate a transient condition worth retrying.
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
# Statuses that mean the request will never succeed as configured: a bad key, a
# key without access to the model, or a model name the provider doesn't know.
_UNAVAILABLE_STATUS = {401, 403, 404}

_TRANSIENT_MARKERS = (
    "temporarily unavailable",
    "service unavailable",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "rate limit",
    "high demand",
    "try again",
    "temporarily",
    "quota",
    "capacity",
    "unavailable",
    "internal server error",
    "please retry",
    "econnreset",
    "remote end closed",
)

# Markers of a provider-side moderation/safety block. These are 400-class errors
# that no amount of retrying the same payload will clear, so they are classified
# apart from both transient failures and genuine client errors: they trigger the
# fallback-model chain and, failing that, ContentPolicyBlocked.
_CONTENT_POLICY_MARKERS = (
    "flagged for possible cybersecurity risk",
    "content_policy_violation",
    "content management policy",
    "responsibleaipolicyviolation",
    "content filter",
    "content_filter",
    "prohibited_content",
    "blocked by the safety filter",
    "safety_settings",
    "blocklist",
)

_UNAVAILABLE_MARKERS = (
    "forbidden",
    "unauthorized",
    "authentication",
    "invalid api key",
    "permission denied",
)


class ContentPolicyBlocked(RuntimeError):
    """The provider's moderation layer refused the request outright.

    Distinct from a transient failure and from a malformed request: the payload
    is well-formed and the endpoint is healthy, the content is simply not one the
    provider will serve. Raised after the retry + fallback-model chain is
    exhausted so callers can DISCARD the sample (exclude it from every metric)
    rather than silently score it as a failed task.
    """


class EmptyCompletionError(RuntimeError):
    """The provider returned a structurally empty response (no ``choices``).

    Retried like a transient failure — this is what surfaced as a bare
    ``list index out of range`` at the ``res.choices[0]`` call sites.
    """


class ProviderUnavailableError(RuntimeError):
    """The provider rejected the credentials or the model (401/403/404).

    Non-transient by construction, so the runner discards the task instead of
    retrying it: with a bad key every task in the suite would otherwise burn its
    full retry budget before failing.
    """


def _status_code(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_content_policy_error(exc: BaseException) -> bool:
    """True when the provider's moderation layer refused the request."""
    if isinstance(exc, ContentPolicyBlocked):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _CONTENT_POLICY_MARKERS)


def _is_unavailable_error(exc: BaseException) -> bool:
    """True for a bad key / no access / unknown model.

    Checked after the moderation test, because some providers report a policy
    block with a 403 and that should be discarded as a policy block rather than
    read as a credentials problem.
    """
    if isinstance(exc, ProviderUnavailableError):
        return True
    if _is_content_policy_error(exc):
        return False
    if _status_code(exc) in _UNAVAILABLE_STATUS:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _UNAVAILABLE_MARKERS)


def _is_transient_error(exc: BaseException) -> bool:
    """True for errors a retry can plausibly recover from.

    Rate limits, 5xx bursts and network blips are transient; an empty response is
    treated the same way. A moderation block and a credentials/model rejection
    are never transient — each has its own path.
    """
    if isinstance(exc, EmptyCompletionError):
        return True
    if _is_content_policy_error(exc) or _is_unavailable_error(exc):
        return False
    status = _status_code(exc)
    if status is not None:
        return status in _TRANSIENT_STATUS
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def _backoff(attempt: int) -> float:
    # Cap the exponent so a large ceiling of retries can't blow up 2**attempt;
    # jitter scales with the base so concurrent tasks don't retry in lockstep.
    exp = min(attempt, 16)
    return min(
        LLM_BACKOFF_CAP, LLM_BACKOFF_BASE * (2 ** exp)
    ) + random.uniform(0, LLM_BACKOFF_BASE)


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _check_non_empty(response: Any, model: str) -> Any:
    """Raise :class:`EmptyCompletionError` if ``response`` has no usable choice.

    Every call site immediately does ``res.choices[0].message``; validating here
    turns a cryptic ``IndexError`` deep in the agent loop into a retryable,
    self-describing failure.
    """
    choices = _get_field(response, "choices", None)
    if not choices:
        raise EmptyCompletionError(
            f"provider returned no choices for model {model!r}"
        )
    if _get_field(choices[0], "message", None) is None:
        raise EmptyCompletionError(
            f"provider returned a choice with no message for model {model!r}"
        )
    return response


def _parse_fallback_entry(entry: str, default_provider: Optional[str]):
    """Parse a ``TACT_FALLBACK_MODELS`` entry into ``(provider, model)``."""
    if "|" in entry:
        provider, _, model = entry.partition("|")
        return (provider.strip() or default_provider), model.strip()
    return default_provider, entry.strip()


def _single_provider_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    custom_llm_provider: Optional[str],
    tools: Optional[List[Dict[str, Any]]],
    temperature: Optional[float],
    max_tokens: Optional[int],
    api_base: Optional[str],
    **kwargs: Any,
):
    """One attempt against one (provider, model), with no retry of its own."""
    from litellm import completion as litellm_completion

    call_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "custom_llm_provider": custom_llm_provider,
    }
    if tools is not None:
        call_kwargs["tools"] = tools
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    if api_base is not None:
        call_kwargs["api_base"] = api_base
    call_kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    # Any remaining kwargs (api_key, top_p, response_format, seed, ...) pass
    # straight through to litellm, which reads the provider's key from the
    # environment when none is given explicitly.
    call_kwargs.update(kwargs)
    return _check_non_empty(litellm_completion(**call_kwargs), model)


def _retrying_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    custom_llm_provider: Optional[str],
    tools: Optional[List[Dict[str, Any]]],
    temperature: Optional[float],
    max_tokens: Optional[int],
    api_base: Optional[str],
    **kwargs: Any,
):
    """Retry a litellm call across transient failures, then fallback models.

      * transient failure (429/5xx, timeout, "high demand") or an empty response
        -> exponential backoff + jitter, retried until ``LLM_MAX_WAIT`` elapses
        or ``LLM_MAX_RETRIES`` attempts are spent;
      * moderation block -> a couple of quick retries (classifiers sit near a
        threshold and sampling is non-deterministic), then the next model in
        ``FALLBACK_MODELS``;
      * credentials/model rejected (401/403/404) -> ProviderUnavailableError,
        raised at once so a bad key fails on the first task;
      * genuine client error (400 bad payload) -> raised immediately.

    Once the primary model is exhausted, each fallback model gets the same
    treatment in turn. If every candidate was refused on content-policy grounds,
    :class:`ContentPolicyBlocked` is raised so the caller can discard the sample;
    otherwise the last underlying error surfaces unchanged.
    """
    candidates = [(custom_llm_provider, model)] + [
        _parse_fallback_entry(entry, custom_llm_provider) for entry in FALLBACK_MODELS
    ]
    deadline = time.monotonic() + LLM_MAX_WAIT
    last_exc: Optional[BaseException] = None
    policy_blocked_all = True

    for cand_provider, cand_model in candidates:
        if cand_model != model or cand_provider != custom_llm_provider:
            print(
                f"[llm] falling back to provider={cand_provider!r} "
                f"model={cand_model!r} after {model!r} failed."
            )
        policy_attempts = 0
        for attempt in range(LLM_MAX_RETRIES):
            try:
                return _single_provider_completion(
                    model=cand_model,
                    messages=messages,
                    custom_llm_provider=cand_provider,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_base=api_base,
                    **kwargs,
                )
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if _is_content_policy_error(e):
                    policy_attempts += 1
                    if policy_attempts > LLM_POLICY_RETRIES:
                        print(
                            f"[llm] {cand_model!r} blocked by provider moderation "
                            f"after {policy_attempts} attempt(s): {e}"
                        )
                        break  # move to the next candidate model
                    wait = _backoff(attempt)
                elif _is_unavailable_error(e):
                    # A bad key or an inaccessible model won't fix itself, and
                    # every other task in the suite is about to hit the same
                    # wall. Surface it in the shape the runner discards on.
                    raise ProviderUnavailableError(
                        f"provider rejected model {cand_model!r} "
                        f"(provider={cand_provider!r}). Check that the matching "
                        f"API key is exported and has access to this model. "
                        f"Original error: {e}"
                    ) from e
                elif _is_transient_error(e):
                    policy_blocked_all = False
                    wait = _backoff(attempt)
                else:
                    # Malformed request: no retry, no fallback.
                    raise

                if attempt == LLM_MAX_RETRIES - 1:
                    break
                if time.monotonic() + wait >= deadline:
                    break  # honour the hard wall-clock cap
                print(
                    f"[llm] {type(e).__name__} on {cand_model!r} "
                    f"(attempt {attempt + 1}/{LLM_MAX_RETRIES}); "
                    f"retrying in {wait:.1f}s: {str(e)[:200]}"
                )
                time.sleep(wait)

    assert last_exc is not None
    if policy_blocked_all and _is_content_policy_error(last_exc):
        raise ContentPolicyBlocked(
            f"every candidate model was blocked by provider moderation "
            f"(primary={model!r}, fallbacks={list(FALLBACK_MODELS)}). "
            f"Last error: {last_exc}"
        ) from last_exc
    raise last_exc


def completion(
    model: str,
    messages: List[Dict[str, Any]],
    custom_llm_provider: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = 0.0,
    max_tokens: Optional[int] = None,
    api_base: Optional[str] = None,
    **kwargs: Any,
):
    """Drop-in for ``litellm.completion`` with a provider-agnostic retry layer.

    The provider's API key is read from the environment by litellm; pass
    ``api_key=...`` to override it per call.
    """
    return _retrying_completion(
        model=model,
        messages=messages,
        custom_llm_provider=custom_llm_provider,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        api_base=api_base,
        **kwargs,
    )

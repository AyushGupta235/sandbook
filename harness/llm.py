"""Model client for the generation pipeline.

Two backends behind one interface:

  AgentSDKModel   drives the Claude Agent SDK, which runs the local `claude`
                  CLI and therefore authenticates with the Claude Code
                  subscription rather than a separate API key.
  ScriptedModel   replays recorded replies from disk. The pipeline can be
                  developed and regression-tested with no network, no tokens,
                  and no credentials, which is also what makes the golden-topic
                  tests deterministic.

The pipeline only ever calls `complete()`, so a stage cannot tell which backend
it is talking to.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


MAX_TURNS = 8
MAX_ATTEMPTS = 5          # per call, for transient failures only
BACKOFF_BASE_S = 4.0

# A busy API is not a broken lesson. Dropping a module because the server was
# overloaded for a few seconds would fail closed on the wrong thing, so these
# are retried rather than reported as defects.
TRANSIENT_MARKERS = (
    "529", "overloaded", "rate limit", "rate_limit", "too many requests",
    "503", "502", "504", "timeout", "timed out", "connection reset",
    "temporarily", "try again",
)


class ModelError(RuntimeError):
    """The model could not produce a usable reply."""


class ModelAuthError(ModelError):
    """The CLI could not authenticate. Only the user can resolve this."""


class TransientModelError(ModelError):
    """A server-side hiccup. Worth retrying; not the lesson's fault."""


def looks_transient(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in TRANSIENT_MARKERS)


@dataclass
class Reply:
    text: str
    model: str = ""
    cost_usd: float = 0.0


@dataclass
class Call:
    """One request, recorded so a run can be replayed offline."""
    stage: str
    system: str
    prompt: str
    reply: str


class Model(Protocol):
    def complete(self, *, stage: str, system: str, prompt: str,
                 schema: dict | None = None, model: str | None = None) -> Reply:
        ...


# --------------------------------------------------------------- JSON parsing


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_reply(text: str) -> Any:
    """Pull a JSON value out of a model reply.

    Accepts a bare JSON document or one wrapped in a markdown fence, because
    models produce both regardless of instructions. Anything else is an error
    the caller turns into a retry.
    """
    raw = text.strip()
    if not raw:
        raise ModelError("model returned an empty reply")

    candidates = [raw]
    fenced = _FENCE.search(raw)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    # Last resort: the outermost brace span, for replies with stray prose.
    first, last = raw.find("{"), raw.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw[first:last + 1])

    errors = []
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            errors.append(str(e))
    raise ModelError(f"reply was not valid JSON ({errors[0]}); first 300 chars: {raw[:300]!r}")


# ------------------------------------------------------------ Agent SDK model


class AgentSDKModel:
    """Calls Claude through the Agent SDK.

    Deliberately constrained: no tools, no filesystem access, and no project
    settings. The model's whole job is to return one JSON document, so giving
    it an agent's capabilities would only add failure modes.
    """

    DEFAULT_MODEL = "claude-opus-5"

    def __init__(self, *, default_model: str | None = None, record: list[Call] | None = None,
                 on_retry=None):
        self.default_model = default_model or self.DEFAULT_MODEL
        self.record = record  # when set, every call is appended for later replay
        self.total_cost_usd = 0.0
        self.retries = 0
        self._on_retry = on_retry or (lambda *_: None)

    def on_retry(self, stage: str, attempt: int, delay: float, reason: str) -> None:
        self.retries += 1
        self._on_retry(stage, attempt, delay, reason)

    def complete(self, *, stage: str, system: str, prompt: str,
                 schema: dict | None = None, model: str | None = None) -> Reply:
        chosen = model or self.default_model
        last: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                reply = asyncio.run(self._complete_async(system, prompt, schema, chosen))
                break
            except TransientModelError as e:
                last = e
                if attempt == MAX_ATTEMPTS:
                    raise ModelError(
                        f"gave up after {MAX_ATTEMPTS} attempts against a busy API: {e}") from e
                delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
                self.on_retry(stage, attempt, delay, str(e))
                time.sleep(delay)
        else:  # pragma: no cover - the loop always breaks or raises
            raise ModelError(str(last))

        self.total_cost_usd += reply.cost_usd
        if self.record is not None:
            self.record.append(Call(stage=stage, system=system, prompt=prompt, reply=reply.text))
        return reply

    async def _complete_async(self, system: str, prompt: str,
                              schema: dict | None, model: str) -> Reply:
        try:
            from claude_agent_sdk import (
                AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
            )
        except ImportError as e:
            raise ModelError(
                "claude-agent-sdk is not installed. Install it with:\n"
                "  ./.venv/bin/pip install claude-agent-sdk"
            ) from e

        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system,
            tools=[],                 # generation only; the model never acts
            setting_sources=None,     # ignore user/project settings for reproducibility
            # A bound, not a target. With no tools there is nothing to loop on,
            # but the CLI counts a turn for its own bookkeeping and a limit of 1
            # aborts a perfectly ordinary single reply.
            max_turns=MAX_TURNS,
        )
        if schema is not None:
            options.output_format = {"type": "json_schema", "schema": schema}

        chunks: list[str] = []
        payload = ""          # structured output arrives here, not as text blocks
        auth_failed = False
        result = None
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    if getattr(message, "error", None) == "authentication_failed":
                        auth_failed = True
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
                elif isinstance(message, ResultMessage):
                    result = message
                    if isinstance(getattr(message, "result", None), str):
                        payload = message.result
        except Exception as e:
            if auth_failed or "authenticat" in str(e).lower():
                raise ModelAuthError(AUTH_HELP) from e
            detail = f"{type(e).__name__}: {e}"
            partial = "".join(chunks)
            # The CLI reports an overloaded upstream as ordinary assistant text
            # and then raises a generic error, so the marker is in the partial.
            if looks_transient(detail) or looks_transient(partial):
                raise TransientModelError(
                    (partial.strip().splitlines() or [detail])[0][:200]) from e
            raise ModelError(
                f"the model call failed: {detail}"
                + (f" (partial text: {partial[:200]!r})" if partial else "")
            ) from e

        if auth_failed:
            raise ModelAuthError(AUTH_HELP)

        # When output_format is set the reply is delivered on the result rather
        # than as assistant text, so fall back to it before giving up.
        text = "".join(chunks).strip() or payload.strip()
        if not text:
            raise TransientModelError("the model returned no text")
        if looks_transient(text) and len(text) < 400:
            raise TransientModelError(text.splitlines()[0][:200])
        return Reply(
            text=text,
            model=model,
            cost_usd=float(getattr(result, "total_cost_usd", 0.0) or 0.0),
        )


AUTH_HELP = (
    "The Claude Agent SDK could not authenticate.\n\n"
    "It runs the local `claude` CLI, which reports:\n"
    "    Failed to authenticate: OAuth session expired and could not be refreshed\n\n"
    "The stored credential has no refresh token, so it cannot renew itself. "
    "Signing in is something only you can do:\n\n"
    "    claude          then run  /login\n\n"
    "Nothing else in sandbook needs credentials. Verifying and serving lessons, "
    "and the offline pipeline tests, all work without this."
)


# -------------------------------------------------------------- offline model


class ScriptedModel:
    """Replays recorded replies so the pipeline runs without credentials.

    Replies are matched by stage in recorded order. A stage asking for more
    replies than were recorded is an error rather than a silent fallback,
    because a test that quietly invents model output proves nothing.
    """

    def __init__(self, calls: list[Call]):
        self.by_stage: dict[str, list[str]] = {}
        for call in calls:
            self.by_stage.setdefault(call.stage, []).append(call.reply)
        self.cursor: dict[str, int] = {}
        self.total_cost_usd = 0.0

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "ScriptedModel":
        data = json.loads(pathlib.Path(path).read_text())
        return cls([Call(**c) for c in data["calls"]])

    def complete(self, *, stage: str, system: str, prompt: str,
                 schema: dict | None = None, model: str | None = None) -> Reply:
        replies = self.by_stage.get(stage)
        if not replies:
            raise ModelError(f"no scripted replies recorded for stage {stage!r}")
        i = self.cursor.get(stage, 0)
        if i >= len(replies):
            raise ModelError(
                f"stage {stage!r} asked for reply {i + 1} but only {len(replies)} were recorded"
            )
        self.cursor[stage] = i + 1
        return Reply(text=replies[i], model="scripted")


def save_recording(path: str | pathlib.Path, calls: list[Call]) -> None:
    payload = {"calls": [c.__dict__ for c in calls]}
    pathlib.Path(path).write_text(json.dumps(payload, indent=2))

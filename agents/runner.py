"""
Agent runner — the heart of the agentic system.

Implements the tool-use loop via the LiteLLM proxy:
  1. Call the proxy (model="sdlc-router") with a system prompt,
     conversation history, and tools
  2. The Auto Router classifies the first turn and pins the model
     for the rest of the agent session via session_affinity
  3. If the model requests tool calls, execute them and feed results back
  4. Repeat until the model returns a final text response

Model assignment is per-agent per-run:
  session_id = "{PIPELINE_RUN_ID}--{agent_name}"
  The proxy pins the model chosen on turn 1 for all subsequent turns
  of that agent, so reclassification never happens mid-agent.

Local development (no Docker):
  pip install "litellm[proxy]>=1.94.0"
  litellm --config litellm-config.yaml --port 4000
  export LITELLM_PROXY_URL=http://localhost:4000
  export LITELLM_MASTER_KEY=<your-local-key>

CI (GitHub Actions):
  The proxy runs as a service container — see .github/workflows/pipeline.yml.
  LITELLM_PROXY_URL and LITELLM_MASTER_KEY are injected as env vars.
"""

import json
import os
import time
from typing import Any

from tools.definitions import dispatch

MAX_TOKENS            = 16_000
MAX_ITERATIONS        = 80         # safety cap — prevents infinite loops
MODEL_CONTEXT_WINDOW  = 200_000    # used for context utilisation reporting
MAX_TOOL_RESULT_CHARS = 8_000      # caps tool results in message history
_CONTEXT_PRUNE_THRESHOLD = 80_000  # prune history when input exceeds this
_CTX_WARN_PCT  = 50.0
_CTX_ALERT_PCT = 75.0

# Module-level token accumulator — keyed by agent name.
# {"input_tokens": int, "output_tokens": int, "cache_read_tokens": int,
#  "cache_creation_tokens": int, "api_calls": int, "peak_ctx_tokens": int, "model": str}
_token_counts: dict[str, dict[str, Any]] = {}


# ── Langfuse — optional, gracefully disabled if creds are absent ──────────────

def _make_langfuse():
    """
    Return a Langfuse v4 client if credentials are configured, else None.
    The pipeline runs normally without it — observability is opt-in.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host       = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not (public_key and secret_key):
        print("[Langfuse] Tracing disabled — LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set", flush=True)
        return None
    try:
        from langfuse import Langfuse
        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        print(f"[Langfuse] Tracing enabled → {host}", flush=True)
        return client
    except Exception as e:
        print(f"[Langfuse] Init failed — observability disabled: {e}", flush=True)
        return None

_langfuse = _make_langfuse()


def _get_run_id() -> str:
    """
    Shared run ID across all agents in a pipeline run.
    The orchestrator sets PIPELINE_RUN_ID at startup so every agent
    trace is grouped under the same session in Langfuse.
    """
    return os.environ.get("PIPELINE_RUN_ID", "")


# ── Langfuse helpers — all no-ops when _langfuse is None ─────────────────────
# Langfuse v4 uses OTel-based start_observation() instead of the v2
# trace()/generation()/span() methods.

def _lf_open_trace(name: str, model: str, tools: list[dict], initial_message: str):
    if not _langfuse:
        return None
    try:
        trace = _langfuse.start_observation(
            as_type="span",
            name=f"agent:{name}",
            input=initial_message,
            metadata={
                "agent":    name,
                "model":    model,
                "provider": "litellm-proxy",
                "tools":    [t["name"] for t in tools],
            },
        )
        # session_id and tags are trace-level attributes set via OTel in v4
        run_id = _get_run_id()
        if run_id:
            trace._otel_span.set_attribute("session.id", run_id)
        trace._otel_span.set_attribute(
            "langfuse.trace.tags",
            json.dumps([f"model:{model}", "provider:litellm-proxy", f"agent:{name}"]),
        )
        print(
            f"[Langfuse] Trace opened — agent:{name} session={run_id or '(none)'} trace_id={trace.trace_id}",
            flush=True,
        )
        return trace
    except Exception as exc:
        print(f"[Langfuse] trace open failed: {exc}", flush=True)
        return None


def _lf_start_generation(trace, model: str, messages: list):
    """Open a generation span immediately before the LLM call so start time is accurate."""
    if not trace:
        return None
    try:
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), None
        )
        return trace.start_observation(
            as_type="generation",
            name="llm-call",
            model=model,
            input=last_user or messages,
        )
    except Exception as exc:
        print(f"[Langfuse] generation start failed: {exc}", flush=True)
        return None


def _lf_end_generation(
    gen, model: str, output: str,
    in_tok: int, out_tok: int,
    cache_read: int = 0, cache_creation: int = 0,
) -> None:
    """Close a generation span with final model, output, and token counts."""
    if not gen:
        return
    try:
        usage: dict[str, Any] = {"input": in_tok, "output": out_tok}
        if cache_read:
            usage["cache_read_input_tokens"] = cache_read
        if cache_creation:
            usage["cache_creation_input_tokens"] = cache_creation
        gen.update(model=model, output=output, usage_details=usage)
        gen.end()
    except Exception as exc:
        print(f"[Langfuse] generation end failed: {exc}", flush=True)


def _lf_tool_start(trace, tool_name: str, args: dict):
    if not trace:
        return None
    try:
        return trace.start_observation(as_type="span", name=f"tool:{tool_name}", input=args)
    except Exception as exc:
        print(f"[Langfuse] tool span start failed: {exc}", flush=True)
        return None


def _lf_tool_end(span, result: str) -> None:
    if not span:
        return
    try:
        span.update(output=result[:500])
        span.end()
    except Exception as exc:
        print(f"[Langfuse] tool span end failed: {exc}", flush=True)


def _lf_close_trace(trace, *, output: str | None = None, error: str | None = None) -> None:
    if not trace:
        return
    try:
        kwargs: dict = {}
        if output is not None:
            kwargs["output"] = output
        if error is not None:
            kwargs["status_message"] = error
            kwargs["level"] = "ERROR"
        if kwargs:
            trace.update(**kwargs)
        trace.end()
    except Exception as exc:
        print(f"[Langfuse] trace update failed: {exc}", flush=True)
    finally:
        if _langfuse:
            try:
                _langfuse.flush()
                print("[Langfuse] Flush complete — trace sent", flush=True)
            except Exception as exc:
                print(f"[Langfuse] flush failed: {exc}", flush=True)


# ── Token report ──────────────────────────────────────────────────────────────

def get_token_report() -> dict[str, dict[str, Any]]:
    """Return a copy of the accumulated per-agent token usage."""
    return {k: dict(v) for k, v in _token_counts.items()}


# ── Public entry point ────────────────────────────────────────────────────────

def run_agent(
    name: str,
    system_prompt: str,
    initial_message: str,
    tools: list[dict],
    *,
    verbose: bool = True,
) -> str:
    """
    Run a single agent to completion and return its final text response.

    All agents call model="sdlc-router" via the LiteLLM proxy.
    The Auto Router classifies the task on the first turn and pins
    the model for all subsequent turns via session_affinity.

    session_id = "{PIPELINE_RUN_ID}--{agent_name}" scopes the pin to
    this agent within this pipeline run. A different run or a different
    agent always gets a fresh classification on its first turn.

    The actual model used is read from response.model and recorded in
    _token_counts so the token report and dashboard reflect the real
    model, not the router alias.
    """
    proxy_url  = os.environ.get("LITELLM_PROXY_URL",  "http://localhost:4000")
    proxy_key  = os.environ.get("LITELLM_MASTER_KEY", "anything")
    run_id     = _get_run_id()

    # Scoped per agent per run — this is the session affinity key.
    # Format: "run-20260923-101530--Coder"
    session_id = f"{run_id}--{name}" if run_id else name

    from openai import OpenAI
    client = OpenAI(api_key=proxy_key, base_url=proxy_url)

    oai_tools = _convert_tools_for_openai(tools)
    messages  = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": initial_message},
    ]

    _log(name, f"Starting — proxy={proxy_url} session={session_id} tools={[t['name'] for t in tools]}")

    # Placeholder until the proxy tells us the actual model on turn 1
    actual_model = "sdlc-router"

    trace = _lf_open_trace(name, actual_model, tools, initial_message)
    try:
        for iteration in range(MAX_ITERATIONS):
            # Open generation span BEFORE the LLM call so start time is accurate.
            # actual_model is "sdlc-router" on turn 1; updated to the real model below
            # and written into the span via _lf_end_generation.
            gen_obs = _lf_start_generation(trace, actual_model, messages)

            raw = _call_with_retry(
                name,
                lambda: client.chat.completions.with_raw_response.create(
                    model="sdlc-router",
                    messages=messages,
                    tools=oai_tools,
                    max_completion_tokens=MAX_TOKENS,
                    extra_body={"metadata": {"session_id": session_id}},
                ),
            )
            response = raw.parse()

            choice  = response.choices[0]
            message = choice.message

            # x-litellm-model-name carries the real routed model (e.g. "openai/gpt-5.6-luna").
            # response.model stays "sdlc-router" (the alias), so we prefer the header.
            _model_name_raw = raw.headers.get("x-litellm-model-name", "")
            actual_model = (
                _model_name_raw.split("/", 1)[-1] if _model_name_raw
                else getattr(response, "model", "sdlc-router")
            )
            if iteration == 0:
                _tier  = raw.headers.get("x-litellm-complexity-router-tier", "")
                _cause = raw.headers.get("x-litellm-complexity-router-cause", "")
                _log(name, f"Router selected model: {actual_model}  (tier={_tier}, cause={_cause})")

            in_tok           = getattr(response.usage, "prompt_tokens",             0) if response.usage else 0
            out_tok          = getattr(response.usage, "completion_tokens",         0) if response.usage else 0
            cache_read       = getattr(response.usage, "prompt_tokens_details",     None)
            cache_read_tok   = getattr(cache_read,      "cached_tokens",            0) if cache_read else 0
            # Anthropic-native fields surfaced by some LiteLLM builds
            cache_read_tok   = cache_read_tok or getattr(response.usage, "cache_read_input_tokens",     0)
            cache_create_tok = getattr(response.usage, "cache_creation_input_tokens", 0)
            _track_and_log_ctx(name, iteration, in_tok, out_tok, actual_model,
                               cache_read_tok, cache_create_tok)

            text_out = message.content or ""
            _lf_end_generation(gen_obs, actual_model, text_out, in_tok, out_tok,
                               cache_read_tok, cache_create_tok)

            if verbose and text_out:
                _log(name, f"[thinking] {text_out[:200]}{'…' if len(text_out) > 200 else ''}")

            # ── Agent finished ────────────────────────────────────────────────
            finish = choice.finish_reason
            if finish not in ("tool_calls", "tool_use"):
                if finish == "length":
                    _lf_close_trace(trace, error="max_tokens exceeded")
                    raise RuntimeError(
                        f"Agent '{name}' hit max_tokens at iteration {iteration + 1} — "
                        "response was truncated; reduce file reads or increase MAX_TOKENS"
                    )
                _log(name, f"Done after {iteration + 1} iteration(s) — model={actual_model}")
                _lf_close_trace(trace, output=text_out)
                return text_out

            # ── Append assistant turn and execute tool calls ──────────────────
            messages.append({
                "role":       "assistant",
                "content":    message.content,
                "tool_calls": [
                    {
                        "id":       tc.id,
                        "type":     "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (message.tool_calls or [])
                ],
            })

            for tc in (message.tool_calls or []):
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                _log(name, f"  → {tc.function.name}({_fmt_input(args)})")
                tool_span = _lf_tool_start(trace, tc.function.name, args)
                result    = dispatch(tc.function.name, args)
                _lf_tool_end(tool_span, result)
                preview   = result[:300] + ("…" if len(result) > 300 else "")
                _log(name, f"  ← {preview}")

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result[:MAX_TOOL_RESULT_CHARS],
                })

            messages = _maybe_prune(name, messages, in_tok)

        _lf_close_trace(trace, error=f"MAX_ITERATIONS ({MAX_ITERATIONS}) exceeded")
        raise RuntimeError(f"Agent '{name}' hit MAX_ITERATIONS ({MAX_ITERATIONS}) without finishing")

    except RuntimeError:
        raise
    except Exception as exc:
        _lf_close_trace(trace, error=str(exc))
        raise


# ── Helpers ───────────────────────────────────────────────────────────────────

_MAX_RL_RETRIES  = 4
_RL_BASE_DELAY_S = 10   # doubles each attempt: 10s, 20s, 40s, 80s
_COOLDOWN_MIN_S  = 35   # LiteLLM cooldown window is 30s; always wait past it


def _parse_retry_after(exc) -> int | None:
    """Extract 'Try again in N seconds' from a LiteLLM cooldown error body."""
    import re
    try:
        body = exc.response.json() if hasattr(exc, "response") else {}
        msg = body.get("error", {}).get("message", "")
        m = re.search(r"Try again in (\d+) seconds", msg)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _call_with_retry(name: str, fn):
    """Call fn(), retrying on 429 RateLimitError with exponential backoff.

    For LiteLLM 'all_deployments_in_cooldown' errors the backoff is floored
    at _COOLDOWN_MIN_S so we never retry before the cooldown expires.
    """
    from openai import RateLimitError
    for attempt in range(_MAX_RL_RETRIES):
        try:
            return fn()
        except RateLimitError as exc:
            if attempt == _MAX_RL_RETRIES - 1:
                raise
            delay = _RL_BASE_DELAY_S * (2 ** attempt)
            # LiteLLM cooldown: honour the server-advertised wait time
            retry_after = _parse_retry_after(exc)
            if retry_after is not None:
                delay = max(delay, retry_after + 5)
            elif "all_deployments_in_cooldown" in str(exc):
                delay = max(delay, _COOLDOWN_MIN_S)
            _log(name, f"⚠️  Rate limited (attempt {attempt + 1}/{_MAX_RL_RETRIES}) — retrying in {delay}s: {exc}")
            time.sleep(delay)


def _convert_tools_for_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schemas (input_schema) to OpenAI format (parameters)."""
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t.get("description", ""),
                "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _maybe_prune(name: str, messages: list, in_tok: int) -> list:
    """Sliding window: keep messages[0] (system) + messages[1] (initial user
    task) + last 8 messages. Mirrors the previous OpenAI pruning strategy."""
    if in_tok > _CONTEXT_PRUNE_THRESHOLD and len(messages) > 10:
        messages = messages[:2] + messages[-8:]
        _log(name, "⚠️  History pruned — kept system + initial message + last 8 turns")
    return messages


def _track_and_log_ctx(
    name: str,
    iteration: int,
    in_tok: int,
    out_tok: int,
    model: str,
    cache_read_tok: int = 0,
    cache_create_tok: int = 0,
) -> None:
    bucket = _token_counts.setdefault(
        name,
        {"input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "api_calls": 0, "peak_ctx_tokens": 0, "model": model},
    )
    bucket["input_tokens"]        += in_tok
    bucket["output_tokens"]       += out_tok
    bucket["cache_read_tokens"]   += cache_read_tok
    bucket["cache_creation_tokens"] += cache_create_tok
    bucket["api_calls"]           += 1
    bucket["peak_ctx_tokens"]      = max(bucket["peak_ctx_tokens"], in_tok)
    # Always reflect the actual model the proxy used, which may differ
    # from the initial placeholder on the first turn.
    bucket["model"] = model

    ctx_pct   = in_tok / MODEL_CONTEXT_WINDOW * 100
    bar       = "█" * int(ctx_pct / 5) + "░" * (20 - int(ctx_pct / 5))
    flag      = "🔴" if ctx_pct >= _CTX_ALERT_PCT else ("⚠️ " if ctx_pct >= _CTX_WARN_PCT else "  ")
    cache_str = f" | cache read={cache_read_tok:,} write={cache_create_tok:,}" if (cache_read_tok or cache_create_tok) else ""
    _log(
        name,
        f"{flag} iter {iteration + 1:>2} | ctx {in_tok:>6,} / {MODEL_CONTEXT_WINDOW:,} tok"
        f" ({ctx_pct:5.1f}%) [{bar}] | model={model}{cache_str}",
    )


def _log(agent_name: str, msg: str) -> None:
    print(f"[{agent_name}] {msg}", flush=True)


def _fmt_input(inp: dict) -> str:
    s = json.dumps(inp)
    return s[:120] + "…" if len(s) > 120 else s
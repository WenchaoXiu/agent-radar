"""Smoke-test the OpenAI-compatible relay: models, chat, JSON output, tool calling, reasoning_effort.

Usage: uv run python scripts/llm_smoke.py
Prints only pass/fail, token counts and latency; never prints the API key.
"""

import json
import sys
import time

import openai

from agent_radar.config import ModelConfig, load_config, load_dotenv
from agent_radar.llm import LLMClient, parse_json

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        results.append((name, True, detail or ""))
    except openai.APIStatusError as exc:
        results.append((name, False, f"HTTP {exc.status_code}: {str(exc.message)[:200]}"))
    except Exception as exc:  # noqa: BLE001 - report any failure
        results.append((name, False, f"{type(exc).__name__}: {str(exc)[:200]}"))


def main() -> int:
    load_dotenv()
    cfg = load_config()
    client = LLMClient.from_env()
    if client is None:
        print("RELAY_BASE_URL / RELAY_API_KEY missing in .env")
        return 1
    roles = {k: v for k, v in (("classify", cfg.llm.classify), ("agent", cfg.llm.agent), ("writer", cfg.llm.writer)) if v}
    models = sorted({m.model for m in roles.values()})
    raw = client._client

    def list_models():
        ids = {m.id for m in raw.models.list()}
        missing = [m for m in models if m not in ids]
        if missing:
            raise AssertionError(f"not listed: {missing}; {len(ids)} models available")
        return f"{len(ids)} models listed, all configured models present"

    check("models.list", list_models)

    for role, mc in roles.items():
        def basic(mc=mc):
            r = client.chat(mc, [{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=2000)
            text = (r.choices[0].message.content or "").strip().lower()
            assert "pong" in text, f"unexpected reply length {len(text)}"
            return f"{mc.model} effort={mc.effort} ok"

        check(f"chat[{role}]", basic)

    plain = ModelConfig(cfg.llm.classify.model)

    def json_mode():
        r = client.chat(
            plain,
            [{"role": "system", "content": "Return JSON only."},
             {"role": "user", "content": 'Return {"results": [{"n": 1}, {"n": 2}]}'}],
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        data = parse_json(r.choices[0].message.content)
        assert [x["n"] for x in data["results"]] == [1, 2]
        return "response_format=json_object ok"

    check("json_object", json_mode)

    tools = [{
        "type": "function",
        "function": {
            "name": "get_stars",
            "description": "Get the current GitHub star count of a repository.",
            "parameters": {
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "owner/name"}},
                "required": ["repo"],
            },
        },
    }]

    def tool_calling(mc: ModelConfig):
        msgs = [{"role": "user", "content": "How many stars does octo/demo have? Use the tool."}]
        r = client.chat(mc, msgs, tools=tools, max_tokens=2000)
        msg = r.choices[0].message
        assert msg.tool_calls, f"no tool_calls (finish_reason={r.choices[0].finish_reason})"
        call = msg.tool_calls[0]
        assert call.function.name == "get_stars"
        assert json.loads(call.function.arguments).get("repo") == "octo/demo"
        msgs.append(msg.model_dump(exclude_none=True))
        msgs.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps({"stars": 4242})})
        r2 = client.chat(mc, msgs, tools=tools, max_tokens=2000)
        text = r2.choices[0].message.content or ""
        assert "4242" in text.replace(",", ""), "final answer missing tool result"
        return f"{mc.model} effort={mc.effort} round-trip ok"

    check("tool_calling[agent]", lambda: tool_calling(cfg.llm.agent))

    def effort_compare():
        # Needs a non-trivial prompt: with adaptive thinking the model skips reasoning on easy questions.
        prompt = [{"role": "user", "content": "Is 9973 prime? Think carefully, then answer yes or no."}]
        out = []
        for effort in (None, "low", "high"):
            t0 = time.monotonic()
            r = client.chat(ModelConfig(cfg.llm.agent.model, effort), prompt, max_tokens=8000)
            dt = time.monotonic() - t0
            details = getattr(r.usage, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            out.append(f"{effort}: completion={r.usage.completion_tokens} reasoning={reasoning} {dt:.1f}s")
        return "; ".join(out)

    check("reasoning_effort none/low/high", effort_compare)

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    client.log_usage()
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import openai
import pytest
from conftest import make_cfg

from agent_radar.classify import Classifier, clean_readme
from agent_radar.config import LLMConfig, ModelConfig
from agent_radar.llm import LLMClient, LLMUnavailable, parse_json

NOW = datetime(2026, 11, 9, 1, 2, 3)


def reply(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


class FakeLLM:
    """Answers classification batches with a scripted function of the input repo list."""

    def __init__(self, answer):
        self.answer = answer
        self.batches: list[list[str]] = []
        self.payloads: list[dict] = []

    def chat(self, cfg, messages, **kwargs):
        user = messages[-1]["content"]
        payload = json.loads(user.removeprefix("<repos>\n").removesuffix("\n</repos>"))
        self.batches.append([p["repo"] for p in payload])
        self.payloads += payload
        return self.answer(payload)


def item(repo, relevant=True, **overrides):
    base = {
        "repo": repo, "relevant": relevant,
        "category": "Agent 框架" if relevant else None,
        "one_liner_zh": "一个 Agent 框架" if relevant else "",
        "summary_zh": "项目说明。",
        "key_features": ["功能一", "功能二", "功能三"] if relevant else [],
        "target_users": "开发者" if relevant else "",
        "category_reason": "理由。",
    }
    return {**base, **overrides}


def good(relevant_fn=lambda r: True):
    def answer(payload):
        results = [item(p["repo"], relevant_fn(p["repo"])) for p in payload]
        return reply(json.dumps({"results": results}, ensure_ascii=False))
    return answer


def make(answer, names, *, sources=None, labels=None, batch=10, refresh_per_run=20, readme_chars=30000):
    cfg = make_cfg(llm=LLMConfig(
        enabled=True, classify=ModelConfig("m", "low"), classify_batch_size=batch, classify_workers=1,
        readme_chars=readme_chars, refresh_days=30, refresh_per_run=refresh_per_run,
    ))
    meta = {n: {"description": f"desc {n}", "topics": [], "sources": (sources or {}).get(n, ["topic:agents"])}
            for n in names}
    llm = FakeLLM(answer)
    return Classifier(llm, lambda r: "# readme", cfg, meta, labels if labels is not None else {}, NOW), llm


def test_classify_batches_and_stores_all_fields():
    names = [f"o/r{i}" for i in range(12)]
    c, llm = make(good(), names, batch=5)
    c.classify(names)
    assert [len(b) for b in llm.batches] == [5, 5, 2]
    label = c.labels["o/r0"]
    assert label == {
        "relevant": True, "category": "Agent 框架", "one_liner_zh": "一个 Agent 框架",
        "summary_zh": "项目说明。", "key_features": ["功能一", "功能二", "功能三"],
        "target_users": "开发者", "category_reason": "理由。",
        "model": "m", "classified_at": "2026-11-09T01:02:03Z",
    }


def test_readme_always_sent_and_truncated():
    c, llm = make(good(), ["o/a"], readme_chars=5)
    c.get_readme = lambda r: "0123456789"
    c.classify(["o/a"])
    assert llm.payloads[0]["readme"] == "01234"


@pytest.mark.parametrize("override, reason", [
    ({"category": "Not a category"}, "bad_category"),
    ({"summary_zh": ""}, "empty_summary_zh"),
    ({"key_features": []}, "bad_key_features"),
    ({"key_features": ["x"] * 9}, "bad_key_features"),
    ({"one_liner_zh": "长" * 61}, "long_one_liner_zh"),
    ({"category_reason": None}, "empty_category_reason"),
])
def test_invalid_items_retried_then_pending(override, reason, caplog):
    def answer(payload):
        return reply(json.dumps({"results": [
            item(p["repo"], **override) if p["repo"] == "o/bad" else item(p["repo"]) for p in payload]}))

    c, llm = make(answer, ["o/ok", "o/bad"])
    c.classify(["o/ok", "o/bad"])
    assert llm.batches == [["o/ok", "o/bad"], ["o/bad"]]
    assert "o/ok" in c.labels and "o/bad" not in c.labels
    assert c.pending == {"o/bad"}
    assert reason in caplog.text


def test_irrelevant_needs_only_summary_and_reason():
    def answer(payload):
        return reply(json.dumps({"results": [
            {"repo": p["repo"], "relevant": False, "category": "Agent 框架", "one_liner_zh": "x",
             "summary_zh": "面试题库。", "category_reason": "与 Agent 无关。"} for p in payload]}))

    c, _ = make(answer, ["o/x"])
    c.classify(["o/x"])
    label = c.labels["o/x"]
    assert label["relevant"] is False and label["category"] is None and label["one_liner_zh"] == ""
    assert label["key_features"] == [] and label["summary_zh"] == "面试题库。"


def test_unparsable_reply_counts_as_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("agent_radar.classify.FAILURE_DIR", tmp_path)
    c, llm = make(lambda p: reply("sorry, no json"), ["o/a"])
    c.classify(["o/a"])
    assert c.pending == {"o/a"} and len(llm.batches) == 2
    saved = list(tmp_path.iterdir())
    assert len(saved) == 2 and saved[0].read_text() == "sorry, no json"


def test_unparsable_reply_not_saved_in_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr("agent_radar.classify.FAILURE_DIR", tmp_path)
    c, _ = make(lambda p: reply("nope"), ["o/a"])
    c.classify(["o/a"])
    assert list(tmp_path.iterdir()) == []


def test_consecutive_failures_abort():
    names = [f"o/r{i}" for i in range(10)]
    c, _ = make(lambda p: reply("nope"), names, batch=1)
    with pytest.raises(LLMUnavailable):
        c.classify(names)


def test_precise_source_does_not_override_irrelevant():
    c, _ = make(good(lambda r: False), ["o/spam"], sources={"o/spam": ["topic:mcp"]})
    c.classify(["o/spam"])
    assert c.labels["o/spam"]["relevant"] is False
    assert c._repo_input("o/spam")["precise"] is True


def test_ensure_boards_refills_after_irrelevant():
    names = [f"o/r{i}" for i in range(10)]
    irrelevant = {"o/r0", "o/r1"}
    c, llm = make(good(lambda r: r not in irrelevant), names, batch=100)
    c.ensure_boards([(names, 2)])  # window = ceil(2 * 1.5) = 3
    assert llm.batches == [["o/r0", "o/r1", "o/r2"], ["o/r3", "o/r4"]]


def _label(at: str) -> dict:
    return {**item("x"), "classified_at": at}


def test_fresh_labels_are_not_reclassified():
    labels = {"o/a": _label("2026-10-20T00:00:00Z")}  # 20 days old
    c, llm = make(good(), ["o/a", "o/new"], labels=labels)
    c.ensure_boards([(["o/a", "o/new"], 2)])
    assert llm.batches == [["o/new"]]


def test_stale_labels_refreshed_within_budget():
    labels = {f"o/s{i}": _label("2026-09-01T00:00:00Z") for i in range(3)}  # > 30 days old
    names = list(labels)
    c, llm = make(good(), names, labels=labels, refresh_per_run=2)
    c.ensure_boards([(names, 2)])
    assert llm.batches == [["o/s0", "o/s1"]]
    assert c.refreshed == 2
    assert c.labels["o/s2"]["classified_at"] == "2026-09-01T00:00:00Z"


def test_failed_refresh_keeps_old_label_and_listing():
    labels = {"o/s": _label("2026-09-01T00:00:00Z")}
    c, _ = make(lambda p: reply("nope"), ["o/s"], labels=labels)
    c.ensure_boards([(["o/s"], 1)])
    assert c.labels["o/s"]["classified_at"] == "2026-09-01T00:00:00Z"
    assert c.pending == set()


def test_readme_failure_is_tolerated():
    c, llm = make(good(), ["o/a"])

    def boom(repo):
        raise RuntimeError("x")

    c.get_readme = boom
    c.classify(["o/a"])
    assert "o/a" in c.labels and llm.payloads[0]["readme"] == ""


def test_clean_readme_keeps_structure():
    text = (
        '<p align="center"><img src="x"></p>\n![badge](http://b)\n# Title\n\n\n\n'
        "See [docs](https://x.io/a) or https://y.io/b now   \n```sh\nnpm i foo\n```"
    )
    assert clean_readme(text, 1000) == "# Title\n\nSee docs or  now\n```sh\nnpm i foo\n```"
    assert clean_readme("abcdef", 3) == "abc"


@pytest.mark.parametrize("text", ['{"results": []}', '```json\n{"results": []}\n```', 'Here:\n{"results": []}\nDone'])
def test_parse_json_tolerant(text):
    assert parse_json(text) == {"results": []}


def _openai_error(cls, status):
    req = httpx.Request("POST", "http://x")
    return cls("err", response=httpx.Response(status, request=req), body=None)


def test_llm_client_retries_then_unavailable():
    calls = []

    class Completions:
        def create(self, **kw):
            calls.append(kw)
            raise _openai_error(openai.InternalServerError, 502)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    sleeps = []
    client = LLMClient("u", "k", client=fake, sleep=sleeps.append, max_retries=2)
    with pytest.raises(LLMUnavailable):
        client.chat(ModelConfig("m", "high"), [])
    assert len(calls) == 3 and sleeps == [2, 4]
    assert calls[0]["reasoning_effort"] == "high"


def test_llm_client_auth_error_is_fatal():
    class Completions:
        def create(self, **kw):
            raise _openai_error(openai.AuthenticationError, 401)

    client = LLMClient("u", "k", client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
                       sleep=lambda s: None)
    with pytest.raises(LLMUnavailable, match="401"):
        client.chat(ModelConfig("m"), [])


def test_llm_client_omits_effort_when_unset():
    seen = {}

    class Completions:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4))

    client = LLMClient("u", "k", client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    client.chat(ModelConfig("m"), [])
    assert "reasoning_effort" not in seen
    assert client.usage["m"] == {"calls": 1, "prompt": 3, "completion": 4}

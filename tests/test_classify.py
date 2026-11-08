import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import openai
import pytest
from conftest import make_cfg

from agent_radar.classify import Classifier, clean_readme, desc_hash
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

    def chat(self, cfg, messages, **kwargs):
        user = messages[-1]["content"]
        payload = json.loads(user.removeprefix("<repos>\n").removesuffix("\n</repos>"))
        self.batches.append([p["repo"] for p in payload])
        return self.answer(payload)


def good(relevant_fn=lambda r: True):
    def answer(payload):
        results = [
            {"repo": p["repo"], "relevant": relevant_fn(p["repo"]),
             "category": "Agent 框架" if relevant_fn(p["repo"]) else None,
             "one_liner_zh": "一个 Agent 框架" if relevant_fn(p["repo"]) else ""}
            for p in payload
        ]
        return reply(json.dumps({"results": results}, ensure_ascii=False))
    return answer


def make(answer, names, *, sources=None, labels=None, batch=10):
    cfg = make_cfg(llm=LLMConfig(enabled=True, classify=ModelConfig("m", "low"), classify_batch_size=batch))
    meta = {n: {"description": f"desc {n}", "topics": [], "sources": (sources or {}).get(n, ["topic:agents"])}
            for n in names}
    llm = FakeLLM(answer)
    return Classifier(llm, lambda r: "# readme", cfg, meta, labels if labels is not None else {}, NOW), llm


def test_classify_batches_and_stores_labels():
    names = [f"o/r{i}" for i in range(12)]
    c, llm = make(good(), names, batch=5)
    c.classify(names)
    assert [len(b) for b in llm.batches] == [5, 5, 2]
    label = c.labels["o/r0"]
    assert label["relevant"] is True and label["category"] == "Agent 框架"
    assert label["model"] == "m" and label["classified_at"] == "2026-11-09T01:02:03Z"
    assert label["desc_hash"] == desc_hash("desc o/r0")


def test_invalid_items_retried_then_pending():
    def answer(payload):
        results = [{"repo": p["repo"], "relevant": True, "category": "Not a category", "one_liner_zh": "x"}
                   if p["repo"] == "o/bad" else
                   {"repo": p["repo"], "relevant": True, "category": "Coding Agent", "one_liner_zh": "ok"}
                   for p in payload]
        return reply(json.dumps({"results": results}))

    c, llm = make(answer, ["o/ok", "o/bad"])
    c.classify(["o/ok", "o/bad"])
    assert llm.batches == [["o/ok", "o/bad"], ["o/bad"]]
    assert "o/ok" in c.labels and "o/bad" not in c.labels
    assert c.pending == {"o/bad"}


def test_unparsable_reply_counts_as_failure():
    c, llm = make(lambda p: reply("sorry, no json"), ["o/a"])
    c.classify(["o/a"])
    assert c.pending == {"o/a"} and len(llm.batches) == 2


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


def test_irrelevant_clears_category():
    c, _ = make(good(lambda r: False), ["o/x"])
    c.classify(["o/x"])
    assert c.labels["o/x"] == {**c.labels["o/x"], "relevant": False, "category": None, "one_liner_zh": ""}


def test_ensure_boards_refills_after_irrelevant():
    names = [f"o/r{i}" for i in range(10)]
    irrelevant = {"o/r0", "o/r1"}
    c, llm = make(good(lambda r: r not in irrelevant), names, batch=100)
    c.ensure_boards([(names, 2)])  # window = ceil(2 * 1.5) = 3
    assert llm.batches == [["o/r0", "o/r1", "o/r2"], ["o/r3", "o/r4"]]


def test_needs_reclassify_when_description_changes():
    old = {"o/a": {"relevant": True, "category": "Coding Agent", "one_liner_zh": "x", "desc_hash": desc_hash("old")}}
    c, _ = make(good(), ["o/a"], labels=old)
    assert c.needs("o/a")
    c.meta["o/a"]["description"] = "old"
    assert not c.needs("o/a")


def test_readme_failure_is_tolerated():
    c, llm = make(good(), ["o/a"])

    def boom(repo):
        raise RuntimeError("x")

    c.get_readme = boom
    c.classify(["o/a"])
    assert "o/a" in c.labels


def test_clean_readme():
    text = '<p align="center"><img src="x"></p>\n![badge](http://b)\n# Title\n\nHello   world'
    assert clean_readme(text, 100) == "# Title Hello world"
    assert clean_readme("abcdef", 3) == "abc"
    assert clean_readme("See [docs](https://x.io/a) or https://y.io/b now", 100) == "See docs or now"


def test_readme_only_for_thin_descriptions():
    fetched = []
    c, _ = make(good(), ["o/short", "o/long"])
    c.meta["o/long"]["description"] = "A long enough description of an agent framework for building things"
    c.get_readme = lambda r: fetched.append(r) or "# readme"
    c.classify(["o/short", "o/long"])
    assert fetched == ["o/short"] and c.readme_fetched == 1


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

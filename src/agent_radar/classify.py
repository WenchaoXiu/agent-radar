import hashlib
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime

import openai

from .config import ROOT, Config
from .llm import LLMClient, LLMUnavailable, parse_json
from .rank import is_listed

log = logging.getLogger(__name__)

CATEGORIES = (
    "Agent 框架",
    "Coding Agent",
    "Agent 应用",
    "MCP Server/工具",
    "Skills/Prompts",
    "Harness/运行时",
    "教程/Awesome",
)
PROMPT_PATH = ROOT / "prompts" / "classify.md"
ONE_LINER_MAX = 60
BUFFER = 1.5
MAX_ATTEMPTS = 2
MAX_CONSECUTIVE_BATCH_FAILURES = 5
# README text makes the relay add ~16k hidden prompt tokens per repo, so only use it when the description is too thin.
README_DESC_THRESHOLD = 40
MAX_TOKENS = 16000

_HTML = re.compile(r"<[^>]+>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def desc_hash(description: str | None) -> str:
    text = " ".join((description or "").split()).lower()
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def strip_urls(text: str) -> str:
    # Links cost tokens and add nothing to classification.
    return _URL.sub(" ", _LINK.sub(r"\1", text))


def clean_readme(text: str, max_chars: int) -> str:
    text = _IMAGE.sub(" ", text)
    text = _HTML.sub(" ", text)
    text = strip_urls(text)
    return _SPACE.sub(" ", text).strip()[:max_chars]


class Classifier:
    def __init__(
        self,
        llm: LLMClient,
        get_readme: Callable[[str], str],
        cfg: Config,
        meta: dict[str, dict],
        labels: dict[str, dict],
        now: datetime,
    ) -> None:
        if cfg.llm.classify is None:
            raise ValueError("llm.classify model not configured")
        self.llm = llm
        self.get_readme = get_readme
        self.cfg = cfg
        self.meta = meta
        self.labels = labels
        self.now = now
        self.pending: set[str] = set()
        self.classified = 0
        self.readme_fetched = 0
        self._system = PROMPT_PATH.read_text(encoding="utf-8")
        self._precise = set(cfg.precise_queries)
        self._consecutive_failures = 0
        self._reasons: Counter[str] = Counter()

    def needs(self, name: str) -> bool:
        if name in self.pending:
            return False
        label = self.labels.get(name)
        return label is None or label.get("desc_hash") != desc_hash(self.meta[name].get("description"))

    def ensure_boards(self, boards: list[tuple[list[str], int]]) -> None:
        """Classify until the top of every board (plus a buffer) consists of classified, relevant repos."""
        for ordered, size in boards:
            window_size = math.ceil(size * BUFFER)
            while True:
                listed = [n for n in ordered if is_listed(n, self.labels, self.pending)]
                todo = [n for n in listed[:window_size] if self.needs(n)]
                if not todo:
                    break
                self.classify(todo)

    def classify(self, names: list[str]) -> None:
        remaining = list(names)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            failed: list[str] = []
            for i in range(0, len(remaining), self.cfg.llm.classify_batch_size):
                failed += self._batch(remaining[i : i + self.cfg.llm.classify_batch_size])
            remaining = failed
            if not remaining:
                return
            log.warning("classify attempt %d: %d repos failed", attempt, len(remaining))
        self.pending.update(remaining)

    def _batch(self, names: list[str]) -> list[str]:
        payload = [self._repo_input(n) for n in names]
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": "<repos>\n" + json.dumps(payload, ensure_ascii=False) + "\n</repos>"},
        ]
        try:
            resp = self.llm.chat(
                self.cfg.llm.classify, messages, response_format={"type": "json_object"}, max_tokens=MAX_TOKENS
            )
            results = parse_json(resp.choices[0].message.content)["results"]
            if not isinstance(results, list):
                raise TypeError("results is not a list")
        except LLMUnavailable:
            raise
        except openai.APIStatusError as exc:
            log.warning("classify batch failed: HTTP %s", exc.status_code)
            return self._fail(names)
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("classify batch unparsable: %s", type(exc).__name__)
            return self._fail(names)

        accepted = set()
        for item in results:
            label = self._validate(item, names)
            if label is not None:
                name = item["repo"]
                self.labels[name] = label
                accepted.add(name)
        missing = [n for n in names if n not in accepted]
        if missing:
            log.warning("classify batch: %d/%d rejected, reasons %s", len(missing), len(names), dict(self._reasons))
        self._reasons.clear()
        self.classified += len(accepted)
        self._consecutive_failures = 0
        return missing

    def _fail(self, names: list[str]) -> list[str]:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
            raise LLMUnavailable(f"{self._consecutive_failures} consecutive batch failures")
        return names

    def _repo_input(self, name: str) -> dict:
        m = self.meta[name]
        description = strip_urls(m.get("description") or "").strip()
        readme = ""
        if self.cfg.llm.readme_chars > 0 and len(description) < README_DESC_THRESHOLD:
            try:
                readme = clean_readme(self.get_readme(name), self.cfg.llm.readme_chars)
            except Exception as exc:  # noqa: BLE001 - README is optional context
                log.warning("readme %s: %s", name, type(exc).__name__)
        self.readme_fetched += bool(readme)
        return {
            "repo": name,
            "description": description,
            "topics": m.get("topics") or [],
            "precise": self._is_precise(name),
            "readme": readme,
        }

    def _is_precise(self, name: str) -> bool:
        return any(s in self._precise for s in self.meta[name].get("sources") or [])

    def _reject(self, reason: str) -> None:
        self._reasons[reason] += 1

    def _validate(self, item: object, names: list[str]) -> dict | None:
        if not isinstance(item, dict) or item.get("repo") not in names:
            self._reject("unknown_repo")
            return None
        name = item["repo"]
        relevant = item.get("relevant")
        if not isinstance(relevant, bool):
            self._reject("relevant_not_bool")
            return None
        category = item.get("category")
        one_liner = item.get("one_liner_zh")
        if relevant:
            if category not in CATEGORIES:
                self._reject("bad_category")
                return None
            if not isinstance(one_liner, str) or not one_liner.strip():
                self._reject("empty_one_liner")
                return None
            if len(one_liner) > ONE_LINER_MAX:
                self._reject("long_one_liner")
                return None
            one_liner = one_liner.strip()
        else:
            category, one_liner = None, ""
        return {
            "relevant": relevant,
            "category": category,
            "one_liner_zh": one_liner,
            "model": self.cfg.llm.classify.model,
            "classified_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "desc_hash": desc_hash(self.meta[name].get("description")),
        }

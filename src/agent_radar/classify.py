import json
import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

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
FAILURE_DIR = ROOT / "out" / "classify-failures"
BUFFER = 1.5
MAX_ATTEMPTS = 2
MAX_CONSECUTIVE_BATCH_FAILURES = 5
# The relay adds many hidden completion tokens on long inputs (~13k seen for a 32k-char README).
MAX_TOKENS = 32000

LIMITS = {"one_liner_zh": 60, "summary_zh": 500, "target_users": 200, "category_reason": 300}
FEATURE_MAX, FEATURES_MAX_ITEMS = 150, 8

_HTML = re.compile(r"<[^>]+>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_TRAILING = re.compile(r"[ \t]+\n")
_BLANK_LINES = re.compile(r"\n{3,}")


def strip_urls(text: str) -> str:
    return _URL.sub("", _LINK.sub(r"\1", text))


def clean_readme(text: str, max_chars: int) -> str:
    """Drop images, HTML tags and URLs; keep prose, link text, code blocks and line structure."""
    text = _IMAGE.sub("", text)
    text = _HTML.sub("", text)
    text = strip_urls(text)
    text = _TRAILING.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()[:max_chars]


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
        # Latest failure reason per repo (error type / validation reason only, never LLM content).
        self.failures: dict[str, str] = {}
        self.classified = 0
        self.refreshed = 0
        self._refresh_left = cfg.llm.refresh_per_run
        self._tried: set[str] = set()
        self._system = PROMPT_PATH.read_text(encoding="utf-8")
        self._precise = set(cfg.precise_queries)
        self._consecutive_failures = 0
        self._reasons: Counter[str] = Counter()
        self._last_reason = ""
        self._lock = threading.Lock()

    def is_stale(self, label: dict) -> bool:
        try:
            at = datetime.strptime(label["classified_at"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, TypeError, ValueError):
            return True
        return self.now.replace(tzinfo=None) - at > timedelta(days=self.cfg.llm.refresh_days)

    def select(self, names: list[str]) -> list[str]:
        """New repos always; stale ones only while this run's refresh budget lasts."""
        todo = []
        for n in names:
            if n in self._tried:
                continue
            label = self.labels.get(n)
            if label is None:
                todo.append(n)
            elif self._refresh_left > 0 and self.is_stale(label):
                self._refresh_left -= 1
                todo.append(n)
        return todo

    def ensure_boards(self, boards: list[tuple[list[str], int]]) -> None:
        """Classify until the top of every board (plus a buffer) consists of classified, relevant repos."""
        for ordered, size in boards:
            window_size = math.ceil(size * BUFFER)
            while True:
                listed = [n for n in ordered if is_listed(n, self.labels, self.pending)]
                todo = self.select(listed[:window_size])
                if not todo:
                    break
                self.classify(todo)

    def classify(self, names: list[str]) -> None:
        self._tried.update(names)
        size = self.cfg.llm.classify_batch_size
        remaining = list(names)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            batches = [remaining[i : i + size] for i in range(0, len(remaining), size)]
            with ThreadPoolExecutor(max_workers=self.cfg.llm.classify_workers) as pool:
                remaining = [n for failed in pool.map(self._batch, batches) for n in failed]
            if not remaining:
                return
            log.warning("classify attempt %d: %d repos failed", attempt, len(remaining))
        # Stale repos keep their previous label; only never-classified ones are held back.
        self.pending.update(n for n in remaining if n not in self.labels)

    def _batch(self, names: list[str]) -> list[str]:
        payload = [self._repo_input(n) for n in names]
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": "<repos>\n" + json.dumps(payload, ensure_ascii=False) + "\n</repos>"},
        ]
        content = None
        try:
            resp = self.llm.chat(
                self.cfg.llm.classify, messages, response_format={"type": "json_object"}, max_tokens=MAX_TOKENS
            )
            content = resp.choices[0].message.content
            results = parse_json(content)["results"]
            if not isinstance(results, list):
                raise TypeError("results is not a list")
        except LLMUnavailable:
            raise
        except openai.APIStatusError as exc:
            log.warning("classify batch failed: HTTP %s", exc.status_code)
            return self._fail(names, f"http_{exc.status_code}")
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("classify batch unparsable: %s", type(exc).__name__)
            self._save_failure(names, content)
            return self._fail(names, f"unparsable_{type(exc).__name__}")

        with self._lock:
            accepted = set()
            rejected: dict[str, str] = {}
            for item in results:
                label = self._validate(item, names)
                name = item.get("repo") if isinstance(item, dict) else None
                if label is not None:
                    if name in self.labels:
                        self.refreshed += 1
                    self.labels[name] = label
                    accepted.add(name)
                elif name in names:
                    rejected[name] = self._last_reason
            missing = [n for n in names if n not in accepted]
            for n in names:
                if n in accepted:
                    self.failures.pop(n, None)
                else:
                    self.failures[n] = rejected.get(n, "missing_in_reply")
            if missing:
                log.warning("classify batch: %d/%d rejected, reasons %s", len(missing), len(names), dict(self._reasons))
            self._reasons.clear()
            self.classified += len(accepted)
            self._consecutive_failures = 0
        return missing

    def _save_failure(self, names: list[str], content: str | None) -> None:
        # Never logged; in CI the daily workflow uploads this directory as a short-lived artifact.
        if content is None:
            return
        FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w.-]+", "_", names[0])
        path = FAILURE_DIR / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{slug}.txt"
        path.write_text(content, encoding="utf-8")
        log.warning("saved unparsable reply to %s", path)

    def _fail(self, names: list[str], reason: str) -> list[str]:
        with self._lock:
            for n in names:
                self.failures[n] = reason
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                raise LLMUnavailable(f"{self._consecutive_failures} consecutive batch failures")
        return names

    def _repo_input(self, name: str) -> dict:
        m = self.meta[name]
        readme = ""
        if self.cfg.llm.readme_chars > 0:
            try:
                readme = clean_readme(self.get_readme(name), self.cfg.llm.readme_chars)
            except Exception as exc:  # noqa: BLE001 - README is optional context
                log.warning("readme %s: %s", name, type(exc).__name__)
        return {
            "repo": name,
            "description": strip_urls(m.get("description") or "").strip(),
            "topics": m.get("topics") or [],
            "precise": self._is_precise(name),
            "readme": readme,
        }

    def _is_precise(self, name: str) -> bool:
        return any(s in self._precise for s in self.meta[name].get("sources") or [])

    def _reject(self, reason: str) -> None:
        self._reasons[reason] += 1
        self._last_reason = reason

    def _text(self, item: dict, key: str, required: bool) -> str | None:
        """Validated, stripped text field; None means reject."""
        value = item.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            self._reject(f"empty_{key}")
            return None
        if len(value.strip()) > LIMITS[key]:
            self._reject(f"long_{key}")
            return None
        return value.strip()

    def _features(self, item: dict, required: bool) -> list[str] | None:
        value = item.get("key_features")
        if value is None and not required:
            return []
        if (
            not isinstance(value, list)
            or (required and not value)
            or len(value) > FEATURES_MAX_ITEMS
            or not all(isinstance(f, str) and f.strip() and len(f) <= FEATURE_MAX for f in value)
        ):
            self._reject("bad_key_features")
            return None
        return [f.strip() for f in value]

    def _validate(self, item: object, names: list[str]) -> dict | None:
        if not isinstance(item, dict) or item.get("repo") not in names:
            self._reject("unknown_repo")
            return None
        relevant = item.get("relevant")
        if not isinstance(relevant, bool):
            self._reject("relevant_not_bool")
            return None
        category = item.get("category") if relevant else None
        if relevant and category not in CATEGORIES:
            self._reject("bad_category")
            return None

        fields = {
            "one_liner_zh": self._text(item, "one_liner_zh", required=relevant),
            "summary_zh": self._text(item, "summary_zh", required=True),
            "target_users": self._text(item, "target_users", required=relevant),
            "category_reason": self._text(item, "category_reason", required=True),
            "key_features": self._features(item, required=relevant),
        }
        if any(v is None for v in fields.values()):
            return None
        if not relevant:
            fields["one_liner_zh"] = ""
        return {
            "relevant": relevant,
            "category": category,
            **fields,
            "model": self.cfg.llm.classify.model,
            "classified_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

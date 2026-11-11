"""Per-run record (counts, repo names, error types; never LLM content), kept for a few days in data/runs/."""

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .store import DATA_DIR

RUNS_DIR = DATA_DIR / "runs"
KEEP_DAYS = 7
MAX_WARNINGS = 200


class _Collector(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__(level=logging.WARNING)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.sink) < MAX_WARNINGS:
            self.sink.append(f"{record.levelname} {record.name}: {record.getMessage()}")


class RunLog:
    def __init__(self, command: str, started_at: datetime | None = None) -> None:
        self.started_at = started_at or datetime.now(UTC)
        self.data: dict = {"command": command, "started_at": _ts(self.started_at), "status": "running"}
        self.warnings: list[str] = []

    def set(self, section: str, **values) -> None:
        self.data.setdefault(section, {}).update(values)

    @contextmanager
    def capture_warnings(self, logger: str = "agent_radar") -> Iterator[None]:
        """Record WARNING+ messages from our own loggers (they only contain names, counts and error types)."""
        handler = _Collector(self.warnings)
        target = logging.getLogger(logger)
        target.addHandler(handler)
        try:
            yield
        finally:
            target.removeHandler(handler)

    def finish(self, error: BaseException | None = None) -> None:
        now = datetime.now(UTC)
        self.data["finished_at"] = _ts(now)
        self.data["duration_s"] = round((now - self.started_at).total_seconds())
        # Only the exception type: messages from third-party libraries may echo URLs or response bodies.
        self.data["status"] = "ok" if error is None else f"error: {type(error).__name__}"
        self.data["warnings"] = self.warnings

    def save(self, day: date, runs_dir: Path = RUNS_DIR, keep_days: int = KEEP_DAYS) -> Path:
        """Append this run to runs/YYYY-MM-DD.json and drop files older than `keep_days`."""
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{day:%Y-%m-%d}.json"
        runs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        runs.append(self.data)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(runs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
        prune(runs_dir, day, keep_days)
        return path

    def summary_markdown(self) -> str:
        d = self.data
        lines = [f"## agent-radar {d['command']}", "", f"- 状态：{d['status']}，耗时 {d.get('duration_s', '?')} 秒"]
        if c := d.get("collect"):
            lines.append(f"- 采集：{c.get('repos', 0):,} 个仓库，GitHub 请求 {c.get('github_requests', 0)} 次")
        if b := d.get("baselines"):
            for key, label in (("daily", "日增"), ("weekly", "周增")):
                base = b.get(key)
                lines.append(f"- {label}基准：{base['day']}（{base['days']} 天）" if base else f"- {label}基准：无，榜单隐藏")
        if c := d.get("classify"):
            if c.get("skipped"):
                lines.append(f"- 分类：跳过（{c['skipped']}）")
            else:
                lines.append(
                    f"- 分类：新增/刷新 {c.get('classified', 0)} 个（其中刷新 {c.get('refreshed', 0)}），"
                    f"待分类 {len(c.get('pending', []))} 个，失败 {len(c.get('failures', {}))} 个"
                )
                if c.get("aborted"):
                    lines.append(f"- **分类中止**：{c['aborted']}")
                for model, u in (c.get("usage") or {}).items():
                    lines.append(f"- {model}：{u['calls']} 次调用，prompt {u['prompt']:,}，completion {u['completion']:,}")
                if failures := c.get("failures"):
                    lines += ["", "| 失败仓库 | 原因 |", "|---|---|"]
                    lines += [f"| {repo} | {reason} |" for repo, reason in sorted(failures.items())]
        if b := d.get("boards"):
            lines.append("- 榜单：" + "，".join(f"{k} {v}" for k, v in b.items()))
        if self.warnings:
            lines += ["", f"<details><summary>警告 {len(self.warnings)} 条</summary>", "", "```"]
            lines += self.warnings + ["```", "</details>"]
        return "\n".join(lines) + "\n"

    def write_step_summary(self) -> None:
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(self.summary_markdown())


def prune(runs_dir: Path, today: date, keep_days: int = KEEP_DAYS) -> list[Path]:
    """Delete run files dated `keep_days` or more before `today`."""
    cutoff = today - timedelta(days=keep_days)
    removed = []
    for path in runs_dir.glob("*.json"):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day <= cutoff:
            path.unlink()
            removed.append(path)
    return removed


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

import json
import logging
from datetime import UTC, date, datetime

from agent_radar.runlog import RunLog, prune


def test_save_appends_same_day_and_prunes_old(tmp_path):
    for d in ("2026-11-01", "2026-11-04", "2026-11-05"):
        (tmp_path / f"{d}.json").write_text("[]")
    (tmp_path / "notes.json").write_text("[]")
    today = date(2026, 11, 11)
    for status in ("first", "second"):
        run = RunLog("daily", datetime(2026, 11, 11, tzinfo=UTC))
        run.set("collect", repos=1, note=status)
        run.finish()
        path = run.save(today, tmp_path)
    runs = json.loads(path.read_text())
    assert [r["collect"]["note"] for r in runs] == ["first", "second"]
    assert runs[0]["status"] == "ok" and runs[0]["warnings"] == []
    # 7-day retention: 11-04 (= today - 7) and older are dropped.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-11-05.json", "2026-11-11.json", "notes.json"]


def test_prune_returns_removed(tmp_path):
    (tmp_path / "2026-11-01.json").write_text("[]")
    assert [p.name for p in prune(tmp_path, date(2026, 11, 8))] == ["2026-11-01.json"]


def test_finish_records_only_error_type():
    run = RunLog("daily")
    run.finish(RuntimeError("https://secret.example/v1 key=abc"))
    assert run.data["status"] == "error: RuntimeError"
    assert "secret" not in json.dumps(run.data)


def test_capture_warnings_only_own_loggers_and_warning_level():
    run = RunLog("daily")
    with run.capture_warnings():
        logging.getLogger("agent_radar.collect").warning("incomplete results for %r", "topic:mcp")
        logging.getLogger("agent_radar.collect").info("not captured")
        logging.getLogger("httpx").warning("not ours")
    logging.getLogger("agent_radar").warning("after context")
    assert run.warnings == ["WARNING agent_radar.collect: incomplete results for 'topic:mcp'"]


def test_summary_markdown_and_step_summary(tmp_path, monkeypatch):
    run = RunLog("daily")
    run.set("collect", repos=13176, github_requests=350)
    run.set("baselines", daily={"day": "2026-11-10", "days": 1}, weekly=None)
    run.set("classify", classified=9, refreshed=1, pending=["o/p"], failures={"o/p": "http_500"},
            aborted="PermissionDeniedError (HTTP 403)",
            usage={"m": {"calls": 9, "prompt": 359290, "completion": 69709}})
    run.set("boards", total=100, daily=50)
    run.warnings.append("WARNING agent_radar.classify: classify batch failed: HTTP 500")
    run.finish()
    md = run.summary_markdown()
    assert "13,176 个仓库" in md and "日增基准：2026-11-10（1 天）" in md and "周增基准：无" in md
    assert "**分类中止**：PermissionDeniedError (HTTP 403)" in md
    assert "| o/p | http_500 |" in md and "prompt 359,290" in md and "警告 1 条" in md

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run.write_step_summary()
    assert summary.read_text() == md


def test_summary_when_classification_skipped():
    run = RunLog("daily")
    run.set("classify", skipped="RELAY_BASE_URL / RELAY_API_KEY missing")
    run.finish()
    assert "分类：跳过（RELAY_BASE_URL / RELAY_API_KEY missing）" in run.summary_markdown()

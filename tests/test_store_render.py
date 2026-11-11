from datetime import UTC, date, datetime

import pytest

from agent_radar import store
from agent_radar.config import CONFIG_PATH, load_config
from agent_radar.rank import Baseline
from agent_radar.render import md_cell, render_pages, render_readme


def test_snapshot_roundtrip_and_listing(tmp_path):
    snap = {"b/b": [2, 1], "a/a": [10, 3]}
    path = store.save_snapshot(date(2026, 11, 8), snap, tmp_path)
    assert path == tmp_path / "snapshots" / "2026" / "11-08.json"
    assert path.read_text().splitlines()[1] == '"a/a": [10, 3],'
    store.save_snapshot(date(2026, 1, 2), {}, tmp_path)
    assert store.load_snapshot(date(2026, 11, 8), tmp_path) == snap
    assert store.list_snapshot_dates(tmp_path) == [date(2026, 1, 2), date(2026, 11, 8)]


def test_repos_roundtrip(tmp_path):
    meta = {"a/a": {"description": "中文", "sources": ["topic:mcp"]}}
    store.save_repos(meta, tmp_path)
    assert store.load_repos(tmp_path) == meta


def test_md_cell_escapes_untrusted_text():
    assert md_cell("a | b\n<script>") == "a \\| b &lt;script&gt;"
    assert md_cell("[x](http://e)") == "\\[x\\](http://e)"
    assert md_cell(None) == ""
    assert md_cell("x" * 200, 10) == "x" * 9 + "…"


def test_classified_roundtrip(tmp_path):
    labels = {"b/b": {"relevant": False}, "a/a": {"relevant": True, "category": "Agent 框架", "one_liner_zh": "中文"}}
    path = store.save_classified(labels, tmp_path)
    assert path.read_text(encoding="utf-8").splitlines()[1].startswith('"a/a": {')
    assert store.load_classified(tmp_path) == labels
    assert store.load_classified(tmp_path / "missing") == {}


def test_load_real_config():
    cfg = load_config(CONFIG_PATH)
    assert "topic:mcp" in cfg.precise_queries
    assert cfg.min_stars == 100
    assert cfg.boards["total"] == 100
    assert cfg.llm.classify.model
    assert cfg.llm.classify.effort in (None, "low", "medium", "high")


def test_invalid_effort_rejected(tmp_path):
    bad = tmp_path / "radar.yaml"
    bad.write_text("llm:\n  classify: {model: m, effort: max}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="effort"):
        load_config(bad)


def test_render_readme(cfg, today):
    meta = {
        "o/old": {"description": "pipe | here", "language": "Go", "created_at": "2024-01-01T00:00:00Z",
                  "pushed_at": "2026-11-07T00:00:00Z"},
        "o/new": {"description": None, "language": None, "created_at": "2026-11-01T00:00:00Z",
                  "pushed_at": "2026-11-07T00:00:00Z"},
    }
    snap = {"o/old": [12345, 0], "o/new": [150, 0]}
    out = render_readme(cfg, meta, snap, today, datetime(2026, 11, 8, 0, 5))
    assert "| 1 | [o/old](https://github.com/o/old) |  | pipe \\| here | 12,345 |  |" in out
    assert "| 1 | [o/new](https://github.com/o/new) |  |  | 150 |  |" in out
    assert "## 日增" not in out and "## 周增" not in out
    assert "](https://wenchaoxiu.github.io/agent-radar/)" in out and "<details>" not in out
    assert "更新时间：2026-11-08 00:05 UTC（北京时间 2026-11-08 08:05）" in out
    late = render_readme(cfg, meta, snap, today, datetime(2026, 11, 8, 22, 17, tzinfo=UTC))
    assert "2026-11-08 22:17 UTC（北京时间 2026-11-09 06:17）" in late


def test_render_readme_with_labels(cfg, today):
    meta = {n: {"description": "en", "created_at": "2024-01-01T00:00:00Z", "pushed_at": "2026-11-07T00:00:00Z"}
            for n in ("o/a", "o/b", "o/c")}
    snap = {"o/a": [300, 0], "o/b": [200, 0], "o/c": [100, 0]}
    labels = {"o/a": {"relevant": True, "category": "Coding Agent", "one_liner_zh": "写代码的 Agent"},
              "o/b": {"relevant": False}}
    out = render_readme(cfg, meta, snap, today, datetime(2026, 11, 8), labels, skip={"o/c"})
    assert "| 1 | [o/a](https://github.com/o/a) | Coding Agent | 写代码的 Agent | 300 |" in out
    assert "o/b" not in out and "o/c" not in out
    assert "[分类榜](#分类榜)" in out and "### Coding Agent" in out
    assert "| 1 | [o/a](https://github.com/o/a) | 写代码的 Agent | 300 |  |" in out


def test_render_readme_folds_rows_beyond_visible(cfg, today):
    names = [f"o/r{i:02d}" for i in range(25)]
    meta = {n: {"description": "d", "created_at": "2024-01-01T00:00:00Z", "pushed_at": "2026-11-07T00:00:00Z"}
            for n in names}
    snap = {n: [1000 - i, 0] for i, n in enumerate(names)}
    out = render_readme(cfg, meta, snap, today, datetime(2026, 11, 8))
    head, folded = out.split("<details><summary>展开剩余 5 个</summary>")
    assert "| 20 | [o/r19]" in head and "| 21 |" not in head
    assert folded.startswith("\n\n| # | 仓库 | 分类 | 简介 | Star | 日增 |")
    assert "| 21 | [o/r20]" in folded and "| 25 | [o/r24]" in folded
    assert folded.count("</details>") == 1


GROWTH_META = {n: {"description": "d", "language": "Py", "created_at": "2024-01-01T00:00:00Z",
                   "pushed_at": "2026-11-07T00:00:00Z"} for n in ("o/a", "o/b", "o/c")}
GROWTH_SNAP = {"o/a": [1000, 0], "o/b": [500, 0], "o/c": [200, 0]}


def test_render_readme_growth_boards(cfg, today):
    daily = Baseline(date(2026, 11, 5), 3, {"o/a": [990, 0], "o/b": [450, 0]})
    weekly = Baseline(date(2026, 11, 1), 7, {"o/a": [900, 0]})
    out = render_readme(cfg, GROWTH_META, GROWTH_SNAP, today, datetime(2026, 11, 8),
                        daily_base=daily, weekly_base=weekly)
    assert "- [日增 Top 2](#日增)" in out and "- [周增 Top 1](#周增)" in out
    assert "对比 2026-11-05 的快照（实际跨 3 天）" in out
    assert "| 1 | [o/b](https://github.com/o/b) |  | d | +50 | 500 |" in out
    assert "| 2 | [o/a](https://github.com/o/a) |  | d | +10 | 1,000 |" in out
    assert "| 1 | [o/a](https://github.com/o/a) |  | d | +100 | 1,000 |" in out
    assert "| 3 | [o/c](https://github.com/o/c) |  | d | 200 | 🆕 |" in out


def test_render_pages_escapes_untrusted_text(cfg, today):
    meta = {n: dict(v) for n, v in GROWTH_META.items()}
    meta["o/a"]["description"] = "<script>alert(1)</script>"
    labels = {"o/b": {"relevant": True, "category": "Coding Agent", "one_liner_zh": "一句话",
                      "summary_zh": "说明 <b>x</b>", "key_features": ["功能一"]}}
    daily = Baseline(date(2026, 11, 7), 1, {"o/a": [990, 0], "o/b": [450, 0]})
    out = render_pages(cfg, meta, GROWTH_SNAP, today, datetime(2026, 11, 8), labels, daily_base=daily)
    assert "<script>alert" not in out and "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "说明 &lt;b&gt;x&lt;/b&gt;" in out and "<li>功能一</li>" in out
    # Tabs: daily present, weekly hidden without a baseline.
    assert 'data-tab="daily"' in out and 'data-panel="daily"' in out
    assert 'data-tab="weekly"' not in out and 'data-panel="weekly"' not in out
    assert "+50" in out and "🆕" in out
    # Cards: language dot color, search text, category chip, growth bar relative to board max.
    assert 'style="background:#8b949e"' in out  # "Py" is not a known language
    assert 'data-chip="Coding Agent"' in out
    assert 'data-text="o/b 一句话 coding agent"' in out
    assert '<i style="width:100.0%">' in out and '<i style="width:20.0%">' in out
    assert "var VISIBLE = 20;" in out
    assert 'class="card has-info"' in out and 'class="card"' in out  # only cards with details are clickable


def test_render_pages_hero_without_daily_baseline(cfg, today):
    out = render_pages(cfg, GROWTH_META, GROWTH_SNAP, today, datetime(2026, 11, 8))
    assert "今日涨幅第一" not in out and "Star 最多" in out
    assert "2026-11-08 00:00 UTC · 北京时间 2026-11-08 08:00" in out
    assert 'data-tab="daily"' not in out and '<span class="bar">' not in out


def test_load_baseline(tmp_path):
    for d in (date(2026, 11, 1), date(2026, 11, 6), date(2026, 11, 8)):
        store.save_snapshot(d, {"o/a": [d.day, 0]}, tmp_path)
    today = date(2026, 11, 8)
    daily = store.load_baseline(today, 1, tmp_path)
    assert (daily.day, daily.days, daily.snapshot) == (date(2026, 11, 6), 2, {"o/a": [6, 0]})
    assert store.load_baseline(today, 7, tmp_path).day == date(2026, 11, 1)
    assert store.load_baseline(today, 8, tmp_path) is None

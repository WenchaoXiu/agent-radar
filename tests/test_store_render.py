from datetime import date, datetime

from agent_radar import store
from agent_radar.config import CONFIG_PATH, load_config
from agent_radar.render import md_cell, render_readme


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
    assert md_cell(None) == ""
    assert md_cell("x" * 200, 10) == "x" * 9 + "…"


def test_load_real_config():
    cfg = load_config(CONFIG_PATH)
    assert "topic:mcp" in cfg.precise_queries
    assert cfg.min_stars == 100
    assert cfg.boards["total"] == 100


def test_render_readme(cfg, today):
    meta = {
        "o/old": {"description": "pipe | here", "language": "Go", "created_at": "2024-01-01T00:00:00Z",
                  "pushed_at": "2026-11-07T00:00:00Z"},
        "o/new": {"description": None, "language": None, "created_at": "2026-11-01T00:00:00Z",
                  "pushed_at": "2026-11-07T00:00:00Z"},
    }
    snap = {"o/old": [12345, 0], "o/new": [150, 0]}
    out = render_readme(cfg, meta, snap, today, datetime(2026, 11, 8, 0, 5))
    assert "| 1 | [o/old](https://github.com/o/old) | pipe \\| here | 12,345 | Go | 2026-11-07 |" in out
    assert "| 1 | [o/new](https://github.com/o/new) |  | 150 |  | 2026-11-01 |" in out
    assert "2026-11-08 00:05" in out

from conftest import make_cfg

from agent_radar import rank


def meta(created="2025-01-01T00:00:00Z", **kw):
    return {"description": "d", "language": "Python", "created_at": created, "pushed_at": created,
            "fork": False, "archived": False, **kw}


def test_total_sorted_and_limited():
    cfg = make_cfg(boards={"total": 2})
    m = {"a/a": meta(), "b/b": meta(), "c/c": meta()}
    snap = {"a/a": [300, 0], "b/b": [500, 0], "c/c": [100, 0]}
    rows = rank.rank_total(m, snap, cfg)
    assert [(r["rank"], r["repo"], r["stars"]) for r in rows] == [(1, "b/b", 500), (2, "a/a", 300)]


def test_total_excludes_fork_archived_and_blacklist():
    cfg = make_cfg(exclude_repos=frozenset({"bad/repo"}))
    m = {"ok/ok": meta(), "f/f": meta(fork=True), "x/x": meta(archived=True), "Bad/Repo": meta()}
    snap = {k: [1000, 0] for k in m}
    assert [r["repo"] for r in rank.rank_total(m, snap, cfg)] == ["ok/ok"]


def test_total_tie_broken_by_name():
    m = {"b/b": meta(), "a/a": meta()}
    snap = {"b/b": [100, 0], "a/a": [100, 0]}
    assert [r["repo"] for r in rank.rank_total(m, snap, make_cfg())] == ["a/a", "b/b"]


def test_total_ignores_repo_without_metadata(cfg):
    assert rank.rank_total({}, {"a/a": [100, 0]}, cfg) == []


def test_new_window_and_min_stars(cfg, today):
    m = {
        "new/hot": meta(created="2026-11-01T10:00:00Z"),
        "new/edge": meta(created="2026-10-09T00:00:00Z"),   # exactly 30 days ago
        "old/one": meta(created="2026-10-08T23:59:59Z"),
        "new/cold": meta(created="2026-11-05T00:00:00Z"),
    }
    snap = {"new/hot": [900, 0], "new/edge": [200, 0], "old/one": [5000, 0], "new/cold": [99, 0]}
    rows = rank.rank_new(m, snap, cfg, today)
    assert [r["repo"] for r in rows] == ["new/hot", "new/edge"]


def test_new_skips_missing_created_at(cfg, today):
    m = {"a/a": meta(created=None)}
    assert rank.rank_new(m, {"a/a": [500, 0]}, cfg, today) == []

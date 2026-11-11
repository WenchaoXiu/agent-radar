from conftest import make_cfg

from datetime import date

from agent_radar import rank
from agent_radar.rank import Baseline


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


def test_by_category_grouped_ordered_and_limited():
    cfg = make_cfg(boards={"category": 2})
    names = ["a/1", "a/2", "a/3", "b/1", "x/1", "u/1"]
    m = {n: meta() for n in names}
    snap = {"a/1": [100, 0], "a/2": [300, 0], "a/3": [200, 0], "b/1": [50, 0], "x/1": [999, 0], "u/1": [800, 0]}
    labels = {
        "a/1": {"relevant": True, "category": "A"},
        "a/2": {"relevant": True, "category": "A"},
        "a/3": {"relevant": True, "category": "A"},
        "b/1": {"relevant": True, "category": "B"},
        "x/1": {"relevant": False, "category": None},
    }
    boards = rank.rank_by_category(m, snap, cfg, ["B", "C", "A"], labels)
    assert [(c, [(r["rank"], r["repo"]) for r in rows]) for c, rows in boards] == [
        ("B", [(1, "b/1")]),
        ("A", [(1, "a/2"), (2, "a/3")]),
    ]


def test_pick_baseline_day():
    days = [date(2026, 11, 1), date(2026, 11, 4), date(2026, 11, 7), date(2026, 11, 8)]
    today = date(2026, 11, 8)
    assert rank.pick_baseline_day(days, today, 1) == date(2026, 11, 7)
    assert rank.pick_baseline_day(days, today, 7) == date(2026, 11, 1)
    assert rank.pick_baseline_day(days, today, 8) is None
    # Missed cron runs: fall back to the most recent older snapshot.
    assert rank.pick_baseline_day([date(2026, 11, 4), date(2026, 11, 8)], today, 1) == date(2026, 11, 4)


def test_growth_ordering_excludes_new_and_non_positive():
    cfg = make_cfg()
    m = {n: meta() for n in ("a/a", "b/b", "c/c", "d/d", "e/e", "f/f")}
    m["f/f"] = meta(fork=True)
    snap = {"a/a": [1000, 0], "b/b": [500, 0], "c/c": [300, 0], "d/d": [800, 0], "e/e": [900, 0], "f/f": [9999, 0]}
    base = Baseline(date(2026, 11, 7), 1, {
        "a/a": [990, 0], "b/b": [400, 0], "c/c": [200, 0], "d/d": [810, 0], "f/f": [1, 0],
    })  # e/e is new to the pool
    assert rank.ordered_growth(m, snap, cfg, base) == ["b/b", "c/c", "a/a"]  # tie on +100 broken by stars
    assert rank.ordered_growth(m, snap, cfg, None) == []
    rows = rank.rank_growth(m, snap, cfg, base, 2)
    assert [(r["rank"], r["repo"], r["growth"]) for r in rows] == [(1, "b/b", 100), (2, "c/c", 100)]


def test_rows_mark_new_to_pool():
    cfg = make_cfg()
    m = {"a/a": meta(), "b/b": meta()}
    snap = {"a/a": [300, 0], "b/b": [200, 0]}
    base = Baseline(date(2026, 11, 7), 1, {"a/a": [250, 0]})
    rows = rank.rank_total(m, snap, cfg, daily_base=base)
    assert [(r["repo"], r["growth"], r["is_new"]) for r in rows] == [("a/a", 50, False), ("b/b", None, True)]
    no_base = rank.rank_total(m, snap, cfg)
    assert [(r["growth"], r["is_new"]) for r in no_base] == [(None, False), (None, False)]


def test_board_candidates_include_growth_boards_only_with_baseline(cfg, today):
    m = {"a/a": meta()}
    snap = {"a/a": [300, 0]}
    assert len(rank.board_candidates(m, snap, cfg, today)) == 2
    base = Baseline(date(2026, 11, 7), 1, {"a/a": [200, 0]})
    boards = rank.board_candidates(m, snap, cfg, today, base, base)
    assert [(names, size) for names, size in boards[2:]] == [(["a/a"], 50), (["a/a"], 50)]

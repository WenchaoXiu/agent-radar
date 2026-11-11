from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Config

Snapshot = dict[str, list[int]]


@dataclass(frozen=True)
class Baseline:
    """An earlier snapshot to diff against; `days` is the actual span (> min when cron missed runs)."""

    day: date
    days: int
    snapshot: Snapshot


def pick_baseline_day(days: Collection[date], today: date, min_days: int) -> date | None:
    """Most recent snapshot at least `min_days` before `today`."""
    candidates = [d for d in days if (today - d).days >= min_days]
    return max(candidates) if candidates else None


def _eligible(name: str, meta: dict, cfg: Config) -> bool:
    if cfg.exclude_forks and meta.get("fork"):
        return False
    if cfg.exclude_archived and meta.get("archived"):
        return False
    return name.lower() not in cfg.exclude_repos


def _by_stars(names: list[str], snapshot: Snapshot) -> list[str]:
    return sorted(names, key=lambda n: (-snapshot[n][0], n.lower()))


def _pool(meta: dict[str, dict], snapshot: Snapshot, cfg: Config) -> list[str]:
    return [n for n in snapshot if n in meta and _eligible(n, meta[n], cfg)]


def growth(name: str, snapshot: Snapshot, base: Baseline | None) -> int | None:
    """Star gain since the baseline; None when there is no baseline or the repo is new to the pool."""
    if base is None or name not in base.snapshot:
        return None
    return snapshot[name][0] - base.snapshot[name][0]


def ordered_total(meta: dict[str, dict], snapshot: Snapshot, cfg: Config) -> list[str]:
    return _by_stars(_pool(meta, snapshot, cfg), snapshot)


def ordered_new(meta: dict[str, dict], snapshot: Snapshot, cfg: Config, today: date) -> list[str]:
    cutoff = today - timedelta(days=cfg.new_repo_days)
    names = [
        n
        for n in _pool(meta, snapshot, cfg)
        if snapshot[n][0] >= cfg.min_stars
        and (created := _created_date(meta[n])) is not None
        and created >= cutoff
    ]
    return _by_stars(names, snapshot)


def ordered_growth(meta: dict[str, dict], snapshot: Snapshot, cfg: Config, base: Baseline | None) -> list[str]:
    """Repos with positive gain since `base`, largest first; repos new to the pool are excluded."""
    if base is None:
        return []
    gains = {n: g for n in _pool(meta, snapshot, cfg) if (g := growth(n, snapshot, base)) is not None and g > 0}
    return sorted(gains, key=lambda n: (-gains[n], -snapshot[n][0], n.lower()))


def board_candidates(
    meta: dict[str, dict],
    snapshot: Snapshot,
    cfg: Config,
    today: date,
    daily_base: Baseline | None = None,
    weekly_base: Baseline | None = None,
) -> list[tuple[list[str], int]]:
    """(ordered candidates, board size) for every board, used to decide what to classify."""
    boards = [
        (ordered_total(meta, snapshot, cfg), cfg.boards.get("total", 100)),
        (ordered_new(meta, snapshot, cfg, today), cfg.boards.get("new", 50)),
    ]
    if daily_base is not None:
        boards.append((ordered_growth(meta, snapshot, cfg, daily_base), cfg.boards.get("daily", 50)))
    if weekly_base is not None:
        boards.append((ordered_growth(meta, snapshot, cfg, weekly_base), cfg.boards.get("weekly", 50)))
    return boards


def is_listed(name: str, labels: dict[str, dict], skip: Collection[str]) -> bool:
    """Unclassified repos stay listed (LLM disabled/unavailable); known-irrelevant and pending ones do not."""
    return name not in skip and labels.get(name, {}).get("relevant", True)


def build_rows(
    ordered: list[str],
    meta: dict[str, dict],
    snapshot: Snapshot,
    limit: int,
    labels: dict[str, dict] | None = None,
    skip: Collection[str] = (),
    base: Baseline | None = None,
) -> list[dict]:
    """`base` drives the growth column: gain since it, or 🆕 for repos missing from it."""
    labels = labels or {}
    names = [n for n in ordered if is_listed(n, labels, skip)][:limit]
    rows = []
    for i, n in enumerate(names, 1):
        m, label = meta[n], labels.get(n, {})
        rows.append({
            "rank": i,
            "repo": n,
            "stars": snapshot[n][0],
            "growth": growth(n, snapshot, base),
            "is_new": base is not None and n not in base.snapshot,
            "category": label.get("category") or "",
            "summary": label.get("one_liner_zh") or m.get("description") or "",
            "summary_zh": label.get("summary_zh") or "",
            "key_features": label.get("key_features") or [],
            "description": m.get("description") or "",
            "language": m.get("language") or "",
            "created_at": m.get("created_at") or "",
            "pushed_at": m.get("pushed_at") or "",
        })
    return rows


def rank_total(meta, snapshot, cfg: Config, labels=None, skip=(), daily_base=None) -> list[dict]:
    ordered = ordered_total(meta, snapshot, cfg)
    return build_rows(ordered, meta, snapshot, cfg.boards.get("total", 100), labels, skip, daily_base)


def rank_new(meta, snapshot, cfg: Config, today: date, labels=None, skip=(), daily_base=None) -> list[dict]:
    ordered = ordered_new(meta, snapshot, cfg, today)
    return build_rows(ordered, meta, snapshot, cfg.boards.get("new", 50), labels, skip, daily_base)


def rank_growth(meta, snapshot, cfg: Config, base: Baseline | None, size: int, labels=None, skip=()) -> list[dict]:
    return build_rows(ordered_growth(meta, snapshot, cfg, base), meta, snapshot, size, labels, skip, base)


def rank_by_category(
    meta, snapshot, cfg: Config, categories: Collection[str], labels=None, skip=(), daily_base=None
) -> list[tuple[str, list[dict]]]:
    """Top repos per category (in `categories` order) from existing labels; empty categories are omitted."""
    labels = labels or {}
    limit = cfg.boards.get("category", 10)
    ordered = ordered_total(meta, snapshot, cfg)
    boards = []
    for category in categories:
        names = [n for n in ordered if labels.get(n, {}).get("category") == category]
        rows = build_rows(names, meta, snapshot, limit, labels, skip, daily_base)
        if rows:
            boards.append((category, rows))
    return boards


def _created_date(meta: dict) -> date | None:
    created = meta.get("created_at")
    if not created:
        return None
    return datetime.fromisoformat(created.replace("Z", "+00:00")).date()

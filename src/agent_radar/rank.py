from collections.abc import Collection
from datetime import date, datetime, timedelta

from .config import Config


def _eligible(name: str, meta: dict, cfg: Config) -> bool:
    if cfg.exclude_forks and meta.get("fork"):
        return False
    if cfg.exclude_archived and meta.get("archived"):
        return False
    return name.lower() not in cfg.exclude_repos


def _by_stars(names: list[str], snapshot: dict[str, list[int]]) -> list[str]:
    return sorted(names, key=lambda n: (-snapshot[n][0], n.lower()))


def ordered_total(meta: dict[str, dict], snapshot: dict[str, list[int]], cfg: Config) -> list[str]:
    return _by_stars([n for n in snapshot if n in meta and _eligible(n, meta[n], cfg)], snapshot)


def ordered_new(meta: dict[str, dict], snapshot: dict[str, list[int]], cfg: Config, today: date) -> list[str]:
    cutoff = today - timedelta(days=cfg.new_repo_days)
    names = [
        n
        for n in snapshot
        if n in meta
        and _eligible(n, meta[n], cfg)
        and snapshot[n][0] >= cfg.min_stars
        and (created := _created_date(meta[n])) is not None
        and created >= cutoff
    ]
    return _by_stars(names, snapshot)


def board_candidates(
    meta: dict[str, dict], snapshot: dict[str, list[int]], cfg: Config, today: date
) -> list[tuple[list[str], int]]:
    """(ordered candidates, board size) for every board, used to decide what to classify."""
    return [
        (ordered_total(meta, snapshot, cfg), cfg.boards.get("total", 100)),
        (ordered_new(meta, snapshot, cfg, today), cfg.boards.get("new", 50)),
    ]


def is_listed(name: str, labels: dict[str, dict], skip: Collection[str]) -> bool:
    """Unclassified repos stay listed (LLM disabled/unavailable); known-irrelevant and pending ones do not."""
    return name not in skip and labels.get(name, {}).get("relevant", True)


def build_rows(
    ordered: list[str],
    meta: dict[str, dict],
    snapshot: dict[str, list[int]],
    limit: int,
    labels: dict[str, dict] | None = None,
    skip: Collection[str] = (),
) -> list[dict]:
    labels = labels or {}
    names = [n for n in ordered if is_listed(n, labels, skip)][:limit]
    rows = []
    for i, n in enumerate(names, 1):
        m, label = meta[n], labels.get(n, {})
        rows.append({
            "rank": i,
            "repo": n,
            "stars": snapshot[n][0],
            "category": label.get("category") or "",
            "summary": label.get("one_liner_zh") or m.get("description") or "",
            "description": m.get("description") or "",
            "language": m.get("language") or "",
            "created_at": m.get("created_at") or "",
            "pushed_at": m.get("pushed_at") or "",
        })
    return rows


def rank_total(meta, snapshot, cfg: Config, labels=None, skip=()) -> list[dict]:
    return build_rows(ordered_total(meta, snapshot, cfg), meta, snapshot, cfg.boards.get("total", 100), labels, skip)


def rank_new(meta, snapshot, cfg: Config, today: date, labels=None, skip=()) -> list[dict]:
    return build_rows(ordered_new(meta, snapshot, cfg, today), meta, snapshot, cfg.boards.get("new", 50), labels, skip)


def rank_by_category(
    meta, snapshot, cfg: Config, categories: Collection[str], labels=None, skip=()
) -> list[tuple[str, list[dict]]]:
    """Top repos per category (in `categories` order) from existing labels; empty categories are omitted."""
    labels = labels or {}
    limit = cfg.boards.get("category", 10)
    ordered = ordered_total(meta, snapshot, cfg)
    boards = []
    for category in categories:
        names = [n for n in ordered if labels.get(n, {}).get("category") == category]
        rows = build_rows(names, meta, snapshot, limit, labels, skip)
        if rows:
            boards.append((category, rows))
    return boards


def _created_date(meta: dict) -> date | None:
    created = meta.get("created_at")
    if not created:
        return None
    return datetime.fromisoformat(created.replace("Z", "+00:00")).date()

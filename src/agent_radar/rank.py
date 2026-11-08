from datetime import date, datetime, timedelta

from .config import Config


def _eligible(name: str, meta: dict, cfg: Config) -> bool:
    if cfg.exclude_forks and meta.get("fork"):
        return False
    if cfg.exclude_archived and meta.get("archived"):
        return False
    return name.lower() not in cfg.exclude_repos


def _row(rank: int, name: str, meta: dict, stars: int) -> dict:
    return {
        "rank": rank,
        "repo": name,
        "stars": stars,
        "description": meta.get("description") or "",
        "language": meta.get("language") or "",
        "created_at": meta.get("created_at") or "",
        "pushed_at": meta.get("pushed_at") or "",
    }


def _ranked(names: list[str], meta: dict[str, dict], snapshot: dict[str, list[int]], limit: int) -> list[dict]:
    names = sorted(names, key=lambda n: (-snapshot[n][0], n.lower()))[:limit]
    return [_row(i, n, meta[n], snapshot[n][0]) for i, n in enumerate(names, 1)]


def rank_total(meta: dict[str, dict], snapshot: dict[str, list[int]], cfg: Config) -> list[dict]:
    names = [n for n in snapshot if n in meta and _eligible(n, meta[n], cfg)]
    return _ranked(names, meta, snapshot, cfg.boards.get("total", 100))


def rank_new(meta: dict[str, dict], snapshot: dict[str, list[int]], cfg: Config, today: date) -> list[dict]:
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
    return _ranked(names, meta, snapshot, cfg.boards.get("new", 50))


def _created_date(meta: dict) -> date | None:
    created = meta.get("created_at")
    if not created:
        return None
    return datetime.fromisoformat(created.replace("Z", "+00:00")).date()

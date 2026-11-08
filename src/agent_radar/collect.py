import logging
import math
from datetime import date, timedelta
from typing import Protocol

from .config import Config

log = logging.getLogger(__name__)

SEARCH_CAP = 1000
PER_PAGE = 100
INCOMPLETE_RETRIES = 2

META_FIELDS = ("description", "language", "topics", "created_at", "pushed_at", "fork", "archived")


class SearchClient(Protocol):
    def search_repos(self, q: str, page: int = 1, per_page: int = 100) -> dict: ...


def search_all(client: SearchClient, base_query: str, min_stars: int, new_since: str | None = None) -> list[dict]:
    """Return every repo matching base_query with stars >= min_stars, splitting star ranges past the 1000 cap.

    If a range still exceeds the cap and cannot be split, repos created on/after new_since are fetched
    separately so the new-project board stays complete.
    """
    return _search_range(client, base_query, min_stars, None, new_since)


def _fetch(client: SearchClient, q: str, page: int) -> dict:
    for _ in range(INCOMPLETE_RETRIES + 1):
        resp = client.search_repos(q, page=page, per_page=PER_PAGE)
        if not resp.get("incomplete_results"):
            return resp
    log.warning("incomplete results for %r page %d after %d retries", q, page, INCOMPLETE_RETRIES)
    return resp


def _search_range(client: SearchClient, base: str, lo: int, hi: int | None, new_since: str | None) -> list[dict]:
    stars = f"stars:>={lo}" if hi is None else f"stars:{lo}..{hi}"
    q = f"{base} {stars} fork:false archived:false"
    first = _fetch(client, q, 1)
    total = first.get("total_count", 0)
    items = list(first.get("items") or [])
    truncated = False

    if total > SEARCH_CAP:
        top = hi if hi is not None else (items[0]["stargazers_count"] if items else lo)
        if top > lo:
            mid = min(max(lo, int(math.sqrt(lo * top))), top - 1)
            # Upper half keeps the original hi (open-ended at the top level) so repos growing mid-run are not lost.
            return _search_range(client, base, lo, mid, new_since) + _search_range(client, base, mid + 1, hi, new_since)
        log.warning("cannot split %r further (%d results), truncated to %d", q, total, SEARCH_CAP)
        truncated = True

    pages = math.ceil(min(total, SEARCH_CAP) / PER_PAGE)
    for page in range(2, pages + 1):
        batch = _fetch(client, q, page).get("items") or []
        items.extend(batch)
        if len(batch) < PER_PAGE:
            break
    log.info("%s -> %d/%d", q, len(items), total)

    if truncated and new_since:
        items += _search_range(client, f"{base} created:>={new_since}", lo, hi, None)
    return items


def collect(client: SearchClient, cfg: Config, today: date) -> tuple[dict[str, dict], dict[str, list[int]]]:
    """Run all configured queries; return (metadata by repo, snapshot {repo: [stars, forks]})."""
    meta: dict[str, dict] = {}
    snapshot: dict[str, list[int]] = {}
    since = (today - timedelta(days=cfg.new_repo_days)).isoformat()

    for query in cfg.all_queries:
        for item in search_all(client, query, cfg.min_stars, since):
            name = item["full_name"]
            record = meta.get(name)
            if record is None:
                record = {k: item.get(k) for k in META_FIELDS}
                record["sources"] = []
                meta[name] = record
            if query not in record["sources"]:
                record["sources"].append(query)
            snapshot[name] = [int(item.get("stargazers_count", 0)), int(item.get("forks_count", 0))]

    log.info("collected %d repos", len(meta))
    return meta, snapshot

import re

from agent_radar.collect import SEARCH_CAP, collect, search_all


class FakeSearch:
    """Simulates GitHub search over a fixed population of repos."""

    def __init__(self, repos: dict[str, int], created: dict[str, str] | None = None, incomplete: int = 0):
        self.repos = repos
        self.created = created or {}
        self.incomplete = incomplete  # number of leading responses flagged incomplete_results
        self.queries: list[tuple[str, int]] = []

    def search_repos(self, q, page=1, per_page=100):
        self.queries.append((q, page))
        lo, hi = 0, None
        if m := re.search(r"stars:(\d+)\.\.(\d+)", q):
            lo, hi = int(m[1]), int(m[2])
        elif m := re.search(r"stars:>=(\d+)", q):
            lo = int(m[1])
        since = re.search(r"created:>=(\S+)", q)
        matched = [
            (n, s) for n, s in self.repos.items()
            if s >= lo and (hi is None or s <= hi)
            and (not since or self.created.get(n, "") >= since[1])
        ]
        matched.sort(key=lambda x: -x[1])
        start = (page - 1) * per_page
        visible = matched[:SEARCH_CAP]
        items = [
            {"full_name": n, "stargazers_count": s, "forks_count": 1, "description": "x",
             "created_at": self.created.get(n, "2020-01-01T00:00:00Z")}
            for n, s in visible[start:start + per_page]
        ]
        incomplete = self.incomplete > 0
        self.incomplete -= 1
        return {"total_count": len(matched), "incomplete_results": incomplete, "items": items}


def test_search_all_under_cap_paginates():
    repos = {f"o/r{i}": 100 + i for i in range(250)}
    fake = FakeSearch(repos)
    items = search_all(fake, "topic:x", 100)
    assert {i["full_name"] for i in items} == set(repos)
    assert [p for _, p in fake.queries] == [1, 2, 3]


def test_search_all_splits_past_cap():
    repos = {f"o/r{i}": 100 + (i % 3000) for i in range(3500)}
    fake = FakeSearch(repos)
    items = search_all(fake, "topic:x", 100)
    names = [i["full_name"] for i in items]
    assert set(names) == set(repos)
    assert len(names) == len(set(names))
    assert any(".." in q for q, _ in fake.queries)


def test_search_all_unsplittable_truncates():
    repos = {f"o/r{i}": 100 for i in range(1200)}
    items = search_all(FakeSearch(repos), "topic:x", 100)
    assert len(items) == SEARCH_CAP


def test_truncated_range_falls_back_to_new_repos():
    repos = {f"o/r{i}": 100 for i in range(1200)}
    repos["o/fresh"] = 100  # sorted last, beyond the 1000 cap
    fake = FakeSearch(repos, created={"o/fresh": "2026-11-01T00:00:00Z"})
    names = {i["full_name"] for i in search_all(fake, "topic:x", 100, "2026-10-09")}
    assert "o/fresh" in names
    assert any(q.startswith("topic:x created:>=2026-10-09 stars:") for q, _ in fake.queries)


def test_incomplete_results_are_retried():
    fake = FakeSearch({"o/a": 150}, incomplete=2)
    assert [i["full_name"] for i in search_all(fake, "topic:x", 100)] == ["o/a"]
    assert len(fake.queries) == 3


def test_incomplete_results_give_up_after_retries():
    fake = FakeSearch({"o/a": 150}, incomplete=99)
    assert len(search_all(fake, "topic:x", 100)) == 1
    assert len(fake.queries) == 3


def test_query_has_filters():
    fake = FakeSearch({"o/a": 150})
    search_all(fake, "topic:mcp", 100)
    q = fake.queries[0][0]
    assert q == "topic:mcp stars:>=100 fork:false archived:false"


def test_collect_merges_sources_and_snapshot(cfg, today):
    fake = FakeSearch({"o/a": 500, "o/b": 200}, created={"o/b": "2026-11-01T00:00:00Z"})
    meta, snap = collect(fake, cfg, today)
    assert set(meta) == {"o/a", "o/b"}
    assert meta["o/a"]["sources"] == ["topic:mcp", "topic:agents"]
    assert snap == {"o/a": [500, 1], "o/b": [200, 1]}
    assert not any("created:" in q for q, _ in fake.queries)
    assert len(fake.queries) == 2
    assert "stargazers_count" not in meta["o/a"]

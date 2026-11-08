import httpx
import pytest

from agent_radar.github_api import GitHubClient, GitHubError


def make_client(handler, **kw):
    sleeps: list[float] = []
    client = GitHubClient(token="t", transport=httpx.MockTransport(handler), sleep=sleeps.append, **kw)
    return client, sleeps


def test_sends_auth_and_search_params():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total_count": 0, "items": []})

    client, _ = make_client(handler)
    client.search_repos("topic:mcp", page=2)
    assert seen["auth"] == "Bearer t"
    assert seen["params"] == {"q": "topic:mcp", "sort": "stars", "order": "desc", "per_page": "100", "page": "2"}


def test_retries_on_secondary_rate_limit():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(403, headers={"Retry-After": "7"}, json={"message": "secondary rate limit"})
        return httpx.Response(200, json={"total_count": 1, "items": []})

    client, sleeps = make_client(handler)
    assert client.search_repos("q")["total_count"] == 1
    assert sleeps == [8]


def test_retries_on_5xx_then_gives_up():
    client, sleeps = make_client(lambda r: httpx.Response(502), max_retries=2)
    with pytest.raises(GitHubError):
        client.search_repos("q")
    assert sleeps == [1, 2]


def test_plain_403_raises_without_retry():
    client, sleeps = make_client(lambda r: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(GitHubError, match="forbidden"):
        client.search_repos("q")
    assert sleeps == []


def test_throttles_search_requests():
    t = [0.0]
    sleeps: list[float] = []
    client = GitHubClient(
        token="t",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})),
        sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)),
        clock=lambda: t[0],
    )
    client.search_repos("a")
    t[0] += 1.0
    client.search_repos("b")
    assert sleeps == [pytest.approx(1.2)]

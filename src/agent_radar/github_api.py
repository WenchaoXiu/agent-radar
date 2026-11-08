import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
SEARCH_INTERVAL_AUTH = 2.2  # 30 search requests / minute
SEARCH_INTERVAL_ANON = 6.5  # 10 search requests / minute
MAX_WAIT = 300


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-radar",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning("GITHUB_TOKEN not set, using unauthenticated rate limits")
        self._http = httpx.Client(base_url=API_URL, headers=headers, timeout=30, transport=transport)
        self._search_interval = SEARCH_INTERVAL_AUTH if token else SEARCH_INTERVAL_ANON
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_search: float | None = None
        self.request_count = 0

    def close(self) -> None:
        self._http.close()

    def search_repos(self, q: str, page: int = 1, per_page: int = 100) -> dict:
        self._throttle()
        params = {"q": q, "sort": "stars", "order": "desc", "per_page": per_page, "page": page}
        return self._get("/search/repositories", params).json()

    def get_readme(self, repo: str) -> str:
        """Raw README text, or "" if the repo has none."""
        try:
            resp = self._get(f"/repos/{repo}/readme", {}, {"Accept": "application/vnd.github.raw+json"})
        except GitHubError as exc:
            if exc.status == 404:
                return ""
            raise
        return resp.text

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_search is not None:
            wait = self._search_interval - (now - self._last_search)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_search = now

    def _get(self, path: str, params: dict, headers: dict | None = None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.get(path, params=params, headers=headers)
            except httpx.TransportError as exc:
                wait = 2**attempt
                log.warning("network error %s, retry in %ss", type(exc).__name__, wait)
            else:
                self.request_count += 1
                if resp.status_code == 200:
                    return resp
                wait = self._retry_wait(resp, attempt)
                if wait is None:
                    raise GitHubError(f"GET {path} -> {resp.status_code}: {_message(resp)}", resp.status_code)
                log.warning("GET %s -> %s, retry in %ss", path, resp.status_code, wait)
            if attempt < self._max_retries:
                self._sleep(wait)
        raise GitHubError(f"GET {path} failed after {self._max_retries} retries")

    @staticmethod
    def _retry_wait(resp: httpx.Response, attempt: int) -> float | None:
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return min(int(retry_after) + 1, MAX_WAIT)
            if resp.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
                return min(max(reset - time.time(), 0) + 1, MAX_WAIT)
            if resp.status_code == 429:
                return min(60 * (attempt + 1), MAX_WAIT)
            return None
        if resp.status_code >= 500:
            return 2**attempt
        return None


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))[:200]
    except ValueError:
        return resp.text[:200]

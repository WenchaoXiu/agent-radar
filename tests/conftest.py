from datetime import date

import pytest

from agent_radar.config import Config


def make_cfg(**overrides) -> Config:
    base = dict(
        precise_queries=["topic:mcp"],
        broad_queries=["topic:agents"],
        min_stars=100,
        new_repo_days=30,
        boards={"total": 100, "daily": 50, "weekly": 50, "new": 50},
        exclude_repos=frozenset(),
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def cfg() -> Config:
    return make_cfg()


@pytest.fixture
def today() -> date:
    return date(2026, 11, 8)

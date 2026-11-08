import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "radar.yaml"


@dataclass(frozen=True)
class Config:
    precise_queries: list[str]
    broad_queries: list[str]
    min_stars: int
    new_repo_days: int
    boards: dict[str, int]
    exclude_forks: bool = True
    exclude_archived: bool = True
    exclude_repos: frozenset[str] = field(default_factory=frozenset)
    llm: dict = field(default_factory=dict)

    @property
    def all_queries(self) -> list[str]:
        return self.precise_queries + self.broad_queries


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries = raw.get("queries", {})
    thresholds = raw.get("thresholds", {})
    exclude = raw.get("exclude", {})
    return Config(
        precise_queries=list(queries.get("precise") or []),
        broad_queries=list(queries.get("broad") or []),
        min_stars=int(thresholds.get("min_stars", 100)),
        new_repo_days=int(thresholds.get("new_repo_days", 30)),
        boards={k: int(v) for k, v in (raw.get("boards") or {}).items()},
        exclude_forks=bool(exclude.get("forks", True)),
        exclude_archived=bool(exclude.get("archived", True)),
        exclude_repos=frozenset(r.lower() for r in exclude.get("repos") or []),
        llm=dict(raw.get("llm") or {}),
    )


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)

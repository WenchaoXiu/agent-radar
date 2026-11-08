import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "radar.yaml"
EFFORTS = ("low", "medium", "high")


@dataclass(frozen=True)
class ModelConfig:
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = False
    classify: ModelConfig | None = None
    agent: ModelConfig | None = None
    writer: ModelConfig | None = None
    classify_batch_size: int = 10
    readme_chars: int = 2000


def _model_cfg(raw: dict | None) -> ModelConfig | None:
    if not raw or not raw.get("model"):
        return None
    effort = raw.get("effort") or None
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"invalid effort {effort!r}, expected one of {EFFORTS}")
    return ModelConfig(model=str(raw["model"]), effort=effort)


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
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def all_queries(self) -> list[str]:
        return self.precise_queries + self.broad_queries


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries = raw.get("queries", {})
    thresholds = raw.get("thresholds", {})
    exclude = raw.get("exclude", {})
    llm = raw.get("llm") or {}
    return Config(
        precise_queries=list(queries.get("precise") or []),
        broad_queries=list(queries.get("broad") or []),
        min_stars=int(thresholds.get("min_stars", 100)),
        new_repo_days=int(thresholds.get("new_repo_days", 30)),
        boards={k: int(v) for k, v in (raw.get("boards") or {}).items()},
        exclude_forks=bool(exclude.get("forks", True)),
        exclude_archived=bool(exclude.get("archived", True)),
        exclude_repos=frozenset(r.lower() for r in exclude.get("repos") or []),
        llm=LLMConfig(
            enabled=bool(llm.get("enabled", False)),
            classify=_model_cfg(llm.get("classify")),
            agent=_model_cfg(llm.get("agent")),
            writer=_model_cfg(llm.get("writer")),
            classify_batch_size=int(llm.get("classify_batch_size", 10)),
            readme_chars=int(llm.get("readme_chars", 2000)),
        ),
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

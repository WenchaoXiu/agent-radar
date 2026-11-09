from collections.abc import Collection
from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import rank
from .classify import CATEGORIES
from .config import ROOT, Config

TEMPLATE_DIR = ROOT / "templates"
DESC_MAX = 90


def md_cell(value: object, max_len: int = DESC_MAX) -> str:
    """Make untrusted text safe for a single markdown table cell."""
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["cell"] = md_cell
    env.filters["thousands"] = lambda n: f"{n:,}"
    env.filters["day"] = lambda s: (s or "")[:10]
    return env


def render_readme(
    cfg: Config,
    meta: dict[str, dict],
    snapshot: dict[str, list[int]],
    today: date,
    generated_at: datetime,
    labels: dict[str, dict] | None = None,
    skip: Collection[str] = (),
) -> str:
    return _env().get_template("readme.md.j2").render(
        total=rank.rank_total(meta, snapshot, cfg, labels, skip),
        new=rank.rank_new(meta, snapshot, cfg, today, labels, skip),
        categories=rank.rank_by_category(meta, snapshot, cfg, CATEGORIES, labels, skip),
        category_size=cfg.boards.get("category", 10),
        repo_count=len(snapshot),
        min_stars=cfg.min_stars,
        new_repo_days=cfg.new_repo_days,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M"),
    )

from collections.abc import Collection
from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import rank
from .classify import CATEGORIES
from .config import ROOT, Config
from .rank import Baseline

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


def growth_text(row: dict) -> str:
    if row["is_new"]:
        return "🆕"
    return "" if row["growth"] is None else f"{row['growth']:+,}"


def _env(autoescape: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=autoescape,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["cell"] = md_cell
    env.filters["thousands"] = lambda n: f"{n:,}"
    env.filters["day"] = lambda s: (s or "")[:10]
    env.filters["growth"] = growth_text
    return env


def build_context(
    cfg: Config,
    meta: dict[str, dict],
    snapshot: dict[str, list[int]],
    today: date,
    generated_at: datetime,
    labels: dict[str, dict] | None = None,
    skip: Collection[str] = (),
    daily_base: Baseline | None = None,
    weekly_base: Baseline | None = None,
) -> dict:
    return {
        "total": rank.rank_total(meta, snapshot, cfg, labels, skip, daily_base),
        "daily": rank.rank_growth(meta, snapshot, cfg, daily_base, cfg.boards.get("daily", 50), labels, skip),
        "weekly": rank.rank_growth(meta, snapshot, cfg, weekly_base, cfg.boards.get("weekly", 50), labels, skip),
        "new": rank.rank_new(meta, snapshot, cfg, today, labels, skip, daily_base),
        "categories": rank.rank_by_category(meta, snapshot, cfg, CATEGORIES, labels, skip, daily_base),
        "daily_base": daily_base,
        "weekly_base": weekly_base,
        "category_size": cfg.boards.get("category", 10),
        "repo_count": len(snapshot),
        "min_stars": cfg.min_stars,
        "new_repo_days": cfg.new_repo_days,
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M"),
    }


def render_readme(*args, **kwargs) -> str:
    """Same arguments as `build_context`."""
    return _env(autoescape=False).get_template("readme.md.j2").render(build_context(*args, **kwargs))


def render_pages(*args, **kwargs) -> str:
    """Same arguments as `build_context`."""
    return _env(autoescape=True).get_template("pages/index.html.j2").render(build_context(*args, **kwargs))

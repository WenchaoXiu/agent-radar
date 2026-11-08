from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import rank
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
) -> str:
    return _env().get_template("readme.md.j2").render(
        total=rank.rank_total(meta, snapshot, cfg),
        new=rank.rank_new(meta, snapshot, cfg, today),
        repo_count=len(snapshot),
        min_stars=cfg.min_stars,
        new_repo_days=cfg.new_repo_days,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M"),
    )

from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import rank
from .classify import CATEGORIES
from .config import ROOT, Config
from .rank import Baseline

TEMPLATE_DIR = ROOT / "templates"
DESC_MAX = 90
PAGES_URL = "https://wenchaoxiu.github.io/agent-radar/"
VISIBLE_ROWS = 20
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")

# GitHub linguist colors for common languages; others fall back to grey.
LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Go": "#00ADD8",
    "Rust": "#dea584", "Java": "#b07219", "C++": "#f34b7d", "C": "#555555", "C#": "#178600",
    "Swift": "#F05138", "Kotlin": "#A97BFF", "Dart": "#00B4AB", "Ruby": "#701516", "PHP": "#4F5D95",
    "Shell": "#89e051", "HTML": "#e34c26", "CSS": "#663399", "Vue": "#41b883", "Svelte": "#ff3e00",
    "Jupyter Notebook": "#DA5B0B", "Lua": "#000080", "Zig": "#ec915c", "Elixir": "#6e4a7e",
    "Scala": "#c22d40", "MDX": "#fcb32c", "Nix": "#7e7eff",
}
LANG_DEFAULT = "#8b949e"


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
    env.filters["lang_color"] = lambda lang: LANG_COLORS.get(lang or "", LANG_DEFAULT)
    env.globals["max_growth"] = max_growth
    return env


def max_growth(rows: list[dict]) -> int:
    return max((r["growth"] for r in rows if r["growth"] and r["growth"] > 0), default=0)


def _utc(dt: datetime) -> datetime:
    """Naive datetimes are treated as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


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
    labels = labels or {}
    daily = rank.rank_growth(meta, snapshot, cfg, daily_base, cfg.boards.get("daily", 50), labels, skip)
    categories = rank.rank_by_category(meta, snapshot, cfg, CATEGORIES, labels, skip, daily_base)
    return {
        "total": rank.rank_total(meta, snapshot, cfg, labels, skip, daily_base),
        "daily": daily,
        "weekly": rank.rank_growth(meta, snapshot, cfg, weekly_base, cfg.boards.get("weekly", 50), labels, skip),
        "new": rank.rank_new(meta, snapshot, cfg, today, labels, skip, daily_base),
        "categories": categories,
        "category_names": [c for c, _ in categories],
        "top_mover": daily[0] if daily else None,
        "relevant_count": sum(1 for v in labels.values() if v.get("relevant")),
        "pages_url": PAGES_URL,
        "visible_rows": VISIBLE_ROWS,
        "daily_base": daily_base,
        "weekly_base": weekly_base,
        "category_size": cfg.boards.get("category", 10),
        "repo_count": len(snapshot),
        "min_stars": cfg.min_stars,
        "new_repo_days": cfg.new_repo_days,
        "generated_at": _utc(generated_at).strftime("%Y-%m-%d %H:%M"),
        "generated_at_bj": _utc(generated_at).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M"),
    }


def readme_from(ctx: dict) -> str:
    return _env(autoescape=False).get_template("readme.md.j2").render(ctx)


def pages_from(ctx: dict) -> str:
    return _env(autoescape=True).get_template("pages/index.html.j2").render(ctx)


def board_stats(ctx: dict) -> dict:
    """Row counts per board and how many listed repos still lack a category."""
    boards = {k: ctx[k] for k in ("total", "daily", "weekly", "new")}
    listed = {r["repo"] for rows in boards.values() for r in rows}
    unlabeled = {r["repo"] for rows in boards.values() for r in rows if not r["category"]}
    return {
        **{k: len(v) for k, v in boards.items()},
        "categories": len(ctx["categories"]),
        "listed_repos": len(listed),
        "unclassified_listed": len(unlabeled),
    }


def render_readme(*args, **kwargs) -> str:
    """Same arguments as `build_context`."""
    return readme_from(build_context(*args, **kwargs))


def render_pages(*args, **kwargs) -> str:
    """Same arguments as `build_context`."""
    return pages_from(build_context(*args, **kwargs))

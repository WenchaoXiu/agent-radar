import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime

from . import rank, store
from .classify import Classifier
from .collect import collect
from .config import ROOT, Config, load_config, load_dotenv
from .github_api import GitHubClient
from .llm import LLMClient, LLMUnavailable
from .rank import Baseline
from .render import board_stats, build_context, pages_from, readme_from
from .runlog import RUNS_DIR, RunLog

COMMANDS = {
    "daily": "采集、计算榜单、分类并渲染 README / Pages",
    "weekly": "运行 Analyst Agent 生成周报草稿并写入 Notion",
    "classify": "对候选仓库进行 LLM 分类",
    "render": "根据已有数据渲染 README / Pages",
}

OUT_DIR = ROOT / "out"
log = logging.getLogger("agent_radar")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_radar",
        description="AI Agents / MCP / Skills / Harness GitHub 榜单",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        cmd.add_argument(
            "--dry-run",
            action="store_true",
            help="不写入 data/ 和仓库根目录，结果输出到本地 out/",
        )
    return parser


DAILY_MIN_DAYS = 1
WEEKLY_MIN_DAYS = 7


def _baselines(today: date, run: RunLog | None = None) -> tuple[Baseline | None, Baseline | None]:
    daily, weekly = store.load_baseline(today, DAILY_MIN_DAYS), store.load_baseline(today, WEEKLY_MIN_DAYS)
    for name, base in (("daily", daily), ("weekly", weekly)):
        if base is None:
            log.info("%s growth: no baseline snapshot yet, board hidden", name)
        else:
            log.info("%s growth: baseline %s (%d days)", name, base.day, base.days)
        if run is not None:
            run.set("baselines", **{name: base and {"day": base.day.isoformat(), "days": base.days}})
    return daily, weekly


def _write_outputs(args: tuple, kwargs: dict, dry_run: bool, run: RunLog | None = None) -> None:
    """Render README and Pages (docs/index.html) from the same data."""
    ctx = build_context(*args, **kwargs)
    root = OUT_DIR if dry_run else ROOT
    readme = root / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(readme_from(ctx), encoding="utf-8")
    log.info("wrote %s", readme)

    pages = root / "docs" / "index.html"
    pages.parent.mkdir(parents=True, exist_ok=True)
    pages.write_text(pages_from(ctx), encoding="utf-8")
    (pages.parent / ".nojekyll").touch()
    log.info("wrote %s", pages)
    if run is not None:
        run.set("boards", **board_stats(ctx))


def _finish_run(run: RunLog, day: date, dry_run: bool, error: BaseException | None) -> None:
    run.finish(error)
    path = run.save(day, OUT_DIR / "runs" if dry_run else RUNS_DIR)
    log.info("wrote %s", path)
    run.write_step_summary()


def _classify(
    cfg: Config,
    meta: dict,
    snapshot: dict,
    today: date,
    gh: GitHubClient,
    dry_run: bool,
    bases: tuple[Baseline | None, Baseline | None],
    run: RunLog,
) -> tuple[dict[str, dict], set[str]]:
    """Classify board candidates; return (labels, repos to skip). Never raises on LLM outages."""
    labels = store.load_classified()
    if not cfg.llm.enabled:
        run.set("classify", skipped="llm.enabled is false")
        return labels, set()
    llm = LLMClient.from_env()
    if llm is None:
        log.warning("llm.enabled but RELAY_BASE_URL / RELAY_API_KEY missing, skipping classification")
        run.set("classify", skipped="RELAY_BASE_URL / RELAY_API_KEY missing")
        return labels, set()

    classifier = Classifier(llm, gh.get_readme, cfg, meta, labels, datetime.now(UTC))
    aborted = None
    try:
        classifier.ensure_boards(rank.board_candidates(meta, snapshot, cfg, today, *bases))
    except LLMUnavailable as exc:
        aborted = str(exc)
        log.error("classification aborted, unclassified repos stay listed: %s", exc)
    log.info(
        "classified %d repos (%d refreshed), %d pending",
        classifier.classified, classifier.refreshed, len(classifier.pending),
    )
    llm.log_usage()
    run.set(
        "classify",
        classified=classifier.classified,
        refreshed=classifier.refreshed,
        pending=sorted(classifier.pending),
        failures=dict(sorted(classifier.failures.items())),
        aborted=aborted,
        usage={m: dict(u) for m, u in llm.usage.items()},
        total_labels=len(labels),
    )

    if dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        log.info("wrote %s", store.save_classified(labels, OUT_DIR))
    else:
        log.info("wrote %s", store.save_classified(labels))
    return labels, classifier.pending


def cmd_daily(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    run = RunLog("daily", now)
    error = None
    try:
        with run.capture_warnings():
            _daily(args, now, run)
    except Exception as exc:
        error = exc
        raise
    finally:
        _finish_run(run, now.date(), args.dry_run, error)
    return 0


def _daily(args: argparse.Namespace, now: datetime, run: RunLog) -> None:
    cfg = load_config()
    gh = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
    try:
        meta, snapshot = collect(gh, cfg, now.date())
        run.set("collect", queries=len(cfg.all_queries), repos=len(snapshot))
        if not args.dry_run:
            store.save_repos(meta)
            log.info("wrote %s", store.save_snapshot(now.date(), snapshot))
        bases = _baselines(now.date(), run)
        labels, skip = _classify(cfg, meta, snapshot, now.date(), gh, args.dry_run, bases, run)
    finally:
        gh.close()
        run.set("collect", github_requests=gh.request_count)
    log.info("github requests: %d", gh.request_count)

    daily_base, weekly_base = bases
    _write_outputs(
        (cfg, meta, snapshot, now.date(), now, labels, skip),
        {"daily_base": daily_base, "weekly_base": weekly_base},
        args.dry_run,
        run,
    )


def _load_latest() -> tuple[dict, dict, date] | None:
    days = store.list_snapshot_dates()
    meta = store.load_repos()
    if not days or not meta:
        log.error("need data/snapshots and data/repos.json, run `daily` first")
        return None
    log.info("using snapshot %s", days[-1])
    return meta, store.load_snapshot(days[-1]), days[-1]


def cmd_classify(args: argparse.Namespace) -> int:
    loaded = _load_latest()
    if loaded is None:
        return 1
    meta, snapshot, day = loaded
    now = datetime.now(UTC)
    run = RunLog("classify", now)
    error = None
    gh = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
    try:
        with run.capture_warnings():
            _classify(load_config(), meta, snapshot, day, gh, args.dry_run, _baselines(day, run), run)
    except Exception as exc:
        error = exc
        raise
    finally:
        gh.close()
        _finish_run(run, now.date(), args.dry_run, error)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    loaded = _load_latest()
    if loaded is None:
        return 1
    meta, snapshot, day = loaded
    use_out = args.dry_run and (OUT_DIR / "classified.json").is_file()
    labels = store.load_classified(OUT_DIR if use_out else store.DATA_DIR)
    daily_base, weekly_base = _baselines(day)
    _write_outputs(
        (load_config(), meta, snapshot, day, datetime.now(UTC), labels),
        {"daily_base": daily_base, "weekly_base": weekly_base},
        args.dry_run,
    )
    return 0


HANDLERS = {"daily": cmd_daily, "classify": cmd_classify, "render": cmd_render}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    handler = HANDLERS.get(args.command)
    if handler is None:
        print(f"命令 {args.command!r} 尚未实现", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())

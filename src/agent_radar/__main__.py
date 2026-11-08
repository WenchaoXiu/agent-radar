import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from . import rank, store
from .classify import Classifier
from .collect import collect
from .config import ROOT, Config, load_config, load_dotenv
from .github_api import GitHubClient
from .llm import LLMClient, LLMUnavailable
from .render import render_readme

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


def _write_readme(text: str, dry_run: bool) -> Path:
    path = (OUT_DIR if dry_run else ROOT) / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _classify(
    cfg: Config, meta: dict, snapshot: dict, today: date, gh: GitHubClient, dry_run: bool
) -> tuple[dict[str, dict], set[str]]:
    """Classify board candidates; return (labels, repos to skip). Never raises on LLM outages."""
    labels = store.load_classified()
    if not cfg.llm.enabled:
        return labels, set()
    llm = LLMClient.from_env()
    if llm is None:
        log.warning("llm.enabled but RELAY_BASE_URL / RELAY_API_KEY missing, skipping classification")
        return labels, set()

    classifier = Classifier(llm, gh.get_readme, cfg, meta, labels, datetime.now(UTC))
    try:
        classifier.ensure_boards(rank.board_candidates(meta, snapshot, cfg, today))
    except LLMUnavailable as exc:
        log.error("classification aborted, unclassified repos stay listed: %s", exc)
    log.info(
        "classified %d repos, %d pending, %d with README",
        classifier.classified, len(classifier.pending), classifier.readme_fetched,
    )
    llm.log_usage()

    if dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        log.info("wrote %s", store.save_classified(labels, OUT_DIR))
    else:
        log.info("wrote %s", store.save_classified(labels))
    return labels, classifier.pending


def cmd_daily(args: argparse.Namespace) -> int:
    cfg = load_config()
    now = datetime.now(UTC)
    gh = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
    try:
        meta, snapshot = collect(gh, cfg, now.date())
        if not args.dry_run:
            store.save_repos(meta)
            log.info("wrote %s", store.save_snapshot(now.date(), snapshot))
        labels, skip = _classify(cfg, meta, snapshot, now.date(), gh, args.dry_run)
    finally:
        gh.close()
    log.info("github requests: %d", gh.request_count)

    path = _write_readme(render_readme(cfg, meta, snapshot, now.date(), now, labels, skip), args.dry_run)
    log.info("wrote %s", path)
    return 0


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
    gh = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
    try:
        _classify(load_config(), meta, snapshot, day, gh, args.dry_run)
    finally:
        gh.close()
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    loaded = _load_latest()
    if loaded is None:
        return 1
    meta, snapshot, day = loaded
    use_out = args.dry_run and (OUT_DIR / "classified.json").is_file()
    labels = store.load_classified(OUT_DIR if use_out else store.DATA_DIR)
    path = _write_readme(render_readme(load_config(), meta, snapshot, day, datetime.now(UTC), labels), args.dry_run)
    log.info("wrote %s", path)
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

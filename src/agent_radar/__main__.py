import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import store
from .collect import collect
from .config import ROOT, load_config, load_dotenv
from .github_api import GitHubClient
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


def cmd_daily(args: argparse.Namespace) -> int:
    cfg = load_config()
    now = datetime.now(UTC)
    client = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
    try:
        meta, snapshot = collect(client, cfg, now.date())
    finally:
        client.close()
    log.info("github requests: %d", client.request_count)

    if not args.dry_run:
        store.save_repos(meta)
        log.info("wrote %s", store.save_snapshot(now.date(), snapshot))
    path = _write_readme(render_readme(cfg, meta, snapshot, now.date(), now), args.dry_run)
    log.info("wrote %s", path)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    days = store.list_snapshot_dates()
    if not days:
        log.error("no snapshots in data/, run `daily` first")
        return 1
    cfg = load_config()
    snapshot = store.load_snapshot(days[-1])
    meta = store.load_repos()
    now = datetime.now(UTC)
    path = _write_readme(render_readme(cfg, meta, snapshot, days[-1], now), args.dry_run)
    log.info("rendered snapshot %s -> %s", days[-1], path)
    return 0


HANDLERS = {"daily": cmd_daily, "render": cmd_render}


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

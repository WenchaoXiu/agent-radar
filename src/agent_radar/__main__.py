import argparse
import sys

COMMANDS = {
    "daily": "采集、计算榜单、分类并渲染 README / Pages",
    "weekly": "运行 Analyst Agent 生成周报草稿并写入 Notion",
    "classify": "对候选仓库进行 LLM 分类",
    "render": "根据已有数据渲染 README / Pages",
}


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
            help="不写入远端（git / Notion），只输出到本地",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"命令 {args.command!r} 尚未实现", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

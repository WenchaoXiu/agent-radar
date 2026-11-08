import pytest

from agent_radar.__main__ import COMMANDS, build_parser, main


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in COMMANDS:
        assert name in out


@pytest.mark.parametrize("command", list(COMMANDS))
def test_subcommand_accepts_dry_run(command):
    args = build_parser().parse_args([command, "--dry-run"])
    assert args.command == command
    assert args.dry_run is True


def test_missing_command_errors():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0

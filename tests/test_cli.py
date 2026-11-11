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


class _FakeGH:
    request_count = 3

    def __init__(self, token=None):
        pass

    def get_readme(self, name):
        return ""

    def close(self):
        pass


def _patch_daily(monkeypatch, tmp_path, collect):
    import agent_radar.__main__ as cli

    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    monkeypatch.setattr(cli, "GitHubClient", _FakeGH)
    monkeypatch.setattr(cli, "collect", collect)
    monkeypatch.setattr(cli.store, "load_baseline", lambda today, min_days: None)
    monkeypatch.setattr(cli.store, "load_classified", lambda *a: {})
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    monkeypatch.delenv("RELAY_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return cli


def test_daily_dry_run_writes_outputs_and_run_log(monkeypatch, tmp_path):
    import json

    meta = {"o/a": {"description": "d", "created_at": "2024-01-01T00:00:00Z", "pushed_at": "2026-11-07T00:00:00Z"}}
    cli = _patch_daily(monkeypatch, tmp_path, lambda gh, cfg, today: (meta, {"o/a": [500, 1]}))
    assert cli.cmd_daily(build_parser().parse_args(["daily", "--dry-run"])) == 0

    assert (tmp_path / "README.md").is_file() and (tmp_path / "docs" / "index.html").is_file()
    assert (tmp_path / "docs" / ".nojekyll").is_file()
    [run_file] = (tmp_path / "runs").iterdir()
    [run] = json.loads(run_file.read_text())
    assert run["status"] == "ok"
    assert run["collect"] == {"queries": len(cli.load_config().all_queries), "repos": 1, "github_requests": 3}
    assert run["baselines"] == {"daily": None, "weekly": None}
    assert run["classify"] == {"skipped": "RELAY_BASE_URL / RELAY_API_KEY missing"}
    assert run["boards"]["total"] == 1 and run["boards"]["unclassified_listed"] == 1


def test_daily_failure_still_writes_run_log(monkeypatch, tmp_path):
    import json

    def boom(gh, cfg, today):
        raise ConnectionError("https://api.github.com/search?token=nope")

    cli = _patch_daily(monkeypatch, tmp_path, boom)
    with pytest.raises(ConnectionError):
        cli.cmd_daily(build_parser().parse_args(["daily", "--dry-run"]))
    [run_file] = (tmp_path / "runs").iterdir()
    [run] = json.loads(run_file.read_text())
    assert run["status"] == "error: ConnectionError" and "token" not in run_file.read_text()

import json
from datetime import date
from pathlib import Path

from .config import ROOT
from .rank import Baseline, pick_baseline_day

DATA_DIR = ROOT / "data"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_repos(meta: dict[str, dict], data_dir: Path = DATA_DIR) -> Path:
    path = data_dir / "repos.json"
    _write(path, json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return path


def load_repos(data_dir: Path = DATA_DIR) -> dict[str, dict]:
    path = data_dir / "repos.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_classified(labels: dict[str, dict], data_dir: Path = DATA_DIR) -> Path:
    lines = [f"{json.dumps(n)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}" for n, v in sorted(labels.items())]
    path = data_dir / "classified.json"
    _write(path, "{\n" + ",\n".join(lines) + "\n}\n")
    return path


def load_classified(data_dir: Path = DATA_DIR) -> dict[str, dict]:
    path = data_dir / "classified.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def snapshot_path(day: date, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "snapshots" / f"{day:%Y}" / f"{day:%m-%d}.json"


def save_snapshot(day: date, snapshot: dict[str, list[int]], data_dir: Path = DATA_DIR) -> Path:
    # One repo per line keeps daily git diffs small and readable.
    lines = [f"{json.dumps(name)}: [{s}, {f}]" for name, (s, f) in sorted(snapshot.items())]
    path = snapshot_path(day, data_dir)
    _write(path, "{\n" + ",\n".join(lines) + "\n}\n")
    return path


def load_snapshot(day: date, data_dir: Path = DATA_DIR) -> dict[str, list[int]]:
    return json.loads(snapshot_path(day, data_dir).read_text(encoding="utf-8"))


def list_snapshot_dates(data_dir: Path = DATA_DIR) -> list[date]:
    days = []
    for path in (data_dir / "snapshots").glob("*/*.json"):
        month, day = path.stem.split("-")
        days.append(date(int(path.parent.name), int(month), int(day)))
    return sorted(days)


def load_baseline(today: date, min_days: int, data_dir: Path = DATA_DIR) -> Baseline | None:
    day = pick_baseline_day(list_snapshot_dates(data_dir), today, min_days)
    if day is None:
        return None
    return Baseline(day=day, days=(today - day).days, snapshot=load_snapshot(day, data_dir))

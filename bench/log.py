"""The results log: one JSON line per measured turn, keyed by commit and
configuration, so runs can be compared over time."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG = ROOT / "results.jsonl"
TRANSCRIPTS = ROOT / "transcripts"


def commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return out + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return None


def run_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def record(line: dict, log: Path = DEFAULT_LOG) -> None:
    line = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "commit": commit(), **line}
    with log.open("a") as f:
        f.write(json.dumps(line) + "\n")


def save_transcript(run: str, name: str, data) -> Path:
    TRANSCRIPTS.mkdir(exist_ok=True)
    path = TRANSCRIPTS / f"{run}-{name}.json"
    path.write_text(json.dumps(data, indent=1))
    return path

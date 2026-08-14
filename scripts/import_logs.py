"""
One-off backfill: import existing logs/*.json test reports into the SQLite DB.

Each historical JSON report is a single unit run (no batch context), so it lands
as a standalone Unit + its Measurements. Re-running is safe — a report already
imported (matched by transformer + start_time) is skipped.

Usage:
    python -m scripts.import_logs                # import logs/ into data/atb.db
    python -m scripts.import_logs --logs DIR     # a different logs directory
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# Make the project root importable when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.base import init_db, get_session          # noqa: E402
from db.models import Unit                         # noqa: E402
from db.store import ResultStore                   # noqa: E402
from sqlalchemy import select                      # noqa: E402


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def _load_config_loader():
    try:
        from core.config_loader import ConfigLoader
        cl = ConfigLoader()
        cl.scan_and_load()
        return cl
    except Exception:
        return None


def _already_imported(started_at: float | None, transformer_id: str) -> bool:
    if started_at is None:
        return False
    with get_session() as s:
        # Match to the nearest second — ISO round-trip can lose sub-second detail.
        rows = s.scalars(
            select(Unit.started_at).where(Unit.verdict.in_(("PASS", "FAIL", "SKIP")))
        ).all()
        return any(abs((r or 0) - started_at) < 1.0 for r in rows)


def import_report(path: str, store: ResultStore, config_loader) -> str:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    tid = data.get("transformer_id", "unknown")
    start = _epoch(data.get("start_time"))
    end = _epoch(data.get("end_time"))

    if _already_imported(start, tid):
        return "skip"

    steps = []
    for st in data.get("steps", []):
        steps.append(SimpleNamespace(
            step_index=int(st.get("step", 1)) - 1,
            from_winding=st.get("from", ""),
            to_winding=st.get("to", ""),
            measured_voltage=float(st.get("measured_v", 0) or 0),
            expected_voltage=float(st.get("expected_v", 0) or 0),
            tolerance_percent=float(st.get("tolerance_pct", 0) or 0),
            deviation_pct=float(st.get("deviation_pct", 0) or 0),
            passed=(st.get("result") == "PASS"),
            timestamp=_epoch(st.get("timestamp")) or start or 0.0,
            error=st.get("error"),
        ))

    session = SimpleNamespace(
        results=steps, start_time=start or 0.0, end_time=end,
        passed_steps=int(data.get("passed_steps", 0) or 0),
        total_steps=int(data.get("total_steps", len(steps))),
    )
    unit_result = SimpleNamespace(
        unit_number=1, transformer_id=tid,
        passed=(data.get("overall_result") == "PASS"),
        skipped=False, skip_reason="",
        start_time=start or 0.0, end_time=end,
        passed_steps=session.passed_steps, total_steps=session.total_steps,
    )

    config_raw = None
    if config_loader is not None:
        try:
            config_raw = config_loader.get_raw_json(tid)
        except Exception:
            config_raw = None

    uid = store.save_unit_result(
        config_raw=config_raw, session=session, unit_result=unit_result,
        batch=None, operator=data.get("operator", ""),
    )
    return "ok" if uid else "fail"


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill logs/*.json into the ATB database")
    ap.add_argument("--logs", default=str(Path(__file__).parent.parent / "logs"))
    args = ap.parse_args()

    init_db()
    store = ResultStore(get_session)
    config_loader = _load_config_loader()

    files = sorted(glob.glob(os.path.join(args.logs, "*.json")))
    counts = {"ok": 0, "skip": 0, "fail": 0}
    for path in files:
        try:
            counts[import_report(path, store, config_loader)] += 1
        except Exception as exc:
            counts["fail"] += 1
            print(f"  ! {os.path.basename(path)}: {exc}")

    print(f"Imported {counts['ok']} report(s), skipped {counts['skip']} "
          f"already-present, {counts['fail']} failed.")
    print("DB stats:", store.stats())


if __name__ == "__main__":
    main()

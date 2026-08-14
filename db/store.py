"""
ResultStore — high-level persistence for completed test runs.

The test engine already owns the domain objects (TransformerConfig, TestSession,
UnitResult, BatchSession); this store turns a completed unit into rows without
the engine needing to know any SQL. Every public method opens its own short-lived
Session (safe from the background test thread) and is defensive: a persistence
failure is logged and swallowed so it can never break a running test.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from sqlalchemy import func, select

from db.models import (
    AuditLog, Batch, Measurement, Operator, Transformer, Unit,
)

log = logging.getLogger(__name__)


class ResultStore:
    def __init__(self, session_factory: Callable[[], Any]):
        self._session_factory = session_factory

    # ── lookups / upserts ────────────────────────────────────────────────────

    @staticmethod
    def _get_or_create_operator(s, name: str) -> Optional[Operator]:
        name = (name or "").strip()
        if not name:
            return None
        op = s.scalar(select(Operator).where(Operator.name == name))
        if op is None:
            op = Operator(name=name)
            s.add(op)
            s.flush()
        return op

    @staticmethod
    def _upsert_transformer(s, slug: str, config_raw: Optional[Dict]) -> Optional[Transformer]:
        slug = (slug or "").strip()
        if not slug:
            return None
        tf = s.scalar(select(Transformer).where(Transformer.slug == slug))
        cfg = config_raw or {}
        if tf is None:
            tf = Transformer(slug=slug)
            s.add(tf)
        # Keep the catalogue row's queryable columns fresh from the latest config.
        if cfg:
            tf.name = cfg.get("name", tf.name or slug)
            tf.rated_power_va = float(cfg.get("rated_power_va", tf.rated_power_va or 0) or 0)
            tf.rated_frequency_hz = float(cfg.get("rated_frequency_hz", tf.rated_frequency_hz or 50) or 50)
            tf.energize_winding = (cfg.get("auto_matrix") or {}).get("energize_winding")
            tf.config = cfg
        elif not tf.name:
            tf.name = slug
        s.flush()
        return tf

    def _get_or_create_batch(self, s, batch, transformer, operator) -> Optional[Batch]:
        if batch is None:
            return None
        row = s.scalar(select(Batch).where(Batch.uuid == batch.batch_id))
        if row is None:
            row = Batch(
                uuid=batch.batch_id,
                transformer_id=transformer.id if transformer else None,
                operator_id=operator.id if operator else None,
                status="active",
                started_at=batch.start_time,
            )
            s.add(row)
            s.flush()
        return row

    # ── main entry point ─────────────────────────────────────────────────────

    def save_unit_result(
        self, *,
        config_raw: Optional[Dict],
        session: Any,              # TestSession | None
        unit_result: Any,          # UnitResult
        batch: Any = None,         # BatchSession | None
        operator: str = "",
        applied_voltage: Optional[float] = None,
        ratio_factor: Optional[float] = None,
        nominal_excitation_voltage: Optional[float] = None,
    ) -> Optional[int]:
        """Persist one completed (or skipped) unit + its measurements. Returns the
        new Unit id, or None on failure (never raises)."""
        try:
            with self._session_factory() as s:
                op = self._get_or_create_operator(s, operator or getattr(unit_result, "operator", "") or "")
                tf = self._upsert_transformer(s, unit_result.transformer_id, config_raw)
                batch_row = self._get_or_create_batch(s, batch, tf, op)

                if unit_result.skipped:
                    verdict = "SKIP"
                elif unit_result.passed:
                    verdict = "PASS"
                else:
                    verdict = "FAIL"

                started = getattr(unit_result, "start_time", None) or time.time()
                ended = getattr(unit_result, "end_time", None)

                unit = Unit(
                    batch_id=batch_row.id if batch_row else None,
                    transformer_id=tf.id if tf else None,
                    operator_id=op.id if op else None,
                    unit_number=getattr(unit_result, "unit_number", 1),
                    verdict=verdict,
                    skip_reason=getattr(unit_result, "skip_reason", "") or "",
                    applied_voltage=applied_voltage,
                    ratio_factor=ratio_factor,
                    nominal_excitation_voltage=nominal_excitation_voltage,
                    passed_steps=getattr(unit_result, "passed_steps", 0) or 0,
                    total_steps=getattr(unit_result, "total_steps", 0) or 0,
                    config_snapshot=config_raw,
                    started_at=started,
                    ended_at=ended,
                    duration_s=(ended - started) if (ended and started) else None,
                )
                s.add(unit)
                s.flush()

                # Per-step measurements (skipped units usually have no session).
                if session is not None:
                    for r in getattr(session, "results", []) or []:
                        s.add(Measurement(
                            unit_id=unit.id,
                            step_index=r.step_index,
                            from_node=str(r.from_winding),
                            to_node=str(r.to_winding),
                            measured_v=float(r.measured_voltage),
                            expected_v=float(r.expected_voltage),
                            tolerance_pct=float(r.tolerance_percent),
                            deviation_pct=float(r.deviation_pct),
                            passed=bool(r.passed),
                            phase_ok=getattr(r, "phase_ok", None),
                            error=r.error,
                            measured_at=float(r.timestamp),
                        ))

                s.commit()
                return unit.id
        except Exception as exc:  # never break a test because of a DB write
            log.warning(f"[ResultStore] save_unit_result failed: {exc}")
            return None

    def close_batch(self, batch_uuid: str, status: str = "complete") -> None:
        try:
            with self._session_factory() as s:
                row = s.scalar(select(Batch).where(Batch.uuid == batch_uuid))
                if row is not None:
                    row.status = status
                    row.ended_at = time.time()
                    s.commit()
        except Exception as exc:
            log.warning(f"[ResultStore] close_batch failed: {exc}")

    def audit(self, action: str, *, actor: str = "", entity: str = "",
              entity_id: str = "", detail: Optional[Dict] = None) -> None:
        try:
            with self._session_factory() as s:
                s.add(AuditLog(actor=actor, action=action, entity=entity,
                               entity_id=str(entity_id), detail=detail))
                s.commit()
        except Exception as exc:
            log.warning(f"[ResultStore] audit failed: {exc}")

    # ── read helpers (used by a status endpoint / future reports) ─────────────

    def stats(self) -> Dict[str, Any]:
        try:
            with self._session_factory() as s:
                units = s.scalar(select(func.count(Unit.id))) or 0
                passed = s.scalar(select(func.count(Unit.id)).where(Unit.verdict == "PASS")) or 0
                failed = s.scalar(select(func.count(Unit.id)).where(Unit.verdict == "FAIL")) or 0
                skipped = s.scalar(select(func.count(Unit.id)).where(Unit.verdict == "SKIP")) or 0
                batches = s.scalar(select(func.count(Batch.id))) or 0
                transformers = s.scalar(select(func.count(Transformer.id))) or 0
                measurements = s.scalar(select(func.count(Measurement.id))) or 0
                tested = passed + failed
                yield_pct = round(100.0 * passed / tested, 2) if tested else None
                return {
                    "units": units, "passed": passed, "failed": failed,
                    "skipped": skipped, "yield_pct": yield_pct,
                    "batches": batches, "transformers": transformers,
                    "measurements": measurements,
                }
        except Exception as exc:
            log.warning(f"[ResultStore] stats failed: {exc}")
            return {}

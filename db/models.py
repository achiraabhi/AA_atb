"""
ORM schema for the ATB test bench.

Design notes
------------
* **Hybrid storage.** Deep transformer topology stays as a JSON blob
  (``Transformer.config`` / ``Unit.config_snapshot``); only the columns worth
  querying are promoted to real fields. This mirrors the existing JSON configs
  and keeps the schema small without losing queryability.
* **Traceability.** Every ``Unit`` stores ``config_snapshot`` — the exact
  topology + rules it was tested against — so a result stays meaningful even
  after the transformer is later edited.
* **Timestamps** are epoch seconds (float), matching ``time.time()`` used across
  the rest of the system.
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlalchemy import (
    Boolean, Float, ForeignKey, Integer, JSON, String, Text, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


# ── catalogue ──────────────────────────────────────────────────────────────

class Transformer(Base):
    """The transformer catalogue. Mirrors transformers/*.json (Phase 3 will make
    this the source of truth; for now it is upserted as results are recorded)."""
    __tablename__ = "transformers"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)  # == config transformer_id
    name: Mapped[str] = mapped_column(String(200), default="")
    rated_power_va: Mapped[float] = mapped_column(Float, default=0.0)
    rated_frequency_hz: Mapped[float] = mapped_column(Float, default=50.0)
    energize_winding: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)

    versions: Mapped[List["TransformerVersion"]] = relationship(
        back_populates="transformer", cascade="all, delete-orphan")


class TransformerVersion(Base):
    """A snapshot of a transformer's config each time it is saved (history)."""
    __tablename__ = "transformer_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    transformer_id: Mapped[int] = mapped_column(ForeignKey("transformers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    transformer: Mapped[Transformer] = relationship(back_populates="versions")


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


# ── test runs ──────────────────────────────────────────────────────────────

class Batch(Base):
    """A production run: many units of one transformer by one operator."""
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # == BatchSession.batch_id
    transformer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("transformers.id"), nullable=True, index=True)
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("operators.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | complete | aborted
    started_at: Mapped[float] = mapped_column(Float, default=time.time)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    transformer: Mapped[Optional[Transformer]] = relationship()
    operator: Mapped[Optional[Operator]] = relationship()
    units: Mapped[List["Unit"]] = relationship(back_populates="batch")


class Unit(Base):
    """One unit tested — a single pass through the measurement sequence."""
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(40), unique=True, default=_uuid, index=True)
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    transformer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("transformers.id"), nullable=True, index=True)
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("operators.id"), nullable=True)

    unit_number: Mapped[int] = mapped_column(Integer, default=1)
    verdict: Mapped[str] = mapped_column(String(10), index=True)  # PASS | FAIL | SKIP
    skip_reason: Mapped[str] = mapped_column(String(200), default="")

    applied_voltage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ratio_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nominal_excitation_voltage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    passed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    started_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    batch: Mapped[Optional[Batch]] = relationship(back_populates="units")
    transformer: Mapped[Optional[Transformer]] = relationship()
    operator: Mapped[Optional[Operator]] = relationship()
    measurements: Mapped[List["Measurement"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan")


class Measurement(Base):
    """One measurement step within a unit (was TestStepResult)."""
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    from_node: Mapped[str] = mapped_column(String(60), default="")
    to_node: Mapped[str] = mapped_column(String(60), default="")
    measured_v: Mapped[float] = mapped_column(Float, default=0.0)
    expected_v: Mapped[float] = mapped_column(Float, default=0.0)
    tolerance_pct: Mapped[float] = mapped_column(Float, default=0.0)
    deviation_pct: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Winding-polarity check: True = in-phase, False = out-of-phase,
    # NULL = not checked (tap / energizing winding / board unavailable).
    phase_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    measured_at: Mapped[float] = mapped_column(Float, default=time.time)

    unit: Mapped[Unit] = relationship(back_populates="measurements")


# ── operational ────────────────────────────────────────────────────────────

class Setting(Base):
    """Key/value store: serial ports, calibration, thresholds, schema version."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class AuditLog(Base):
    """Who did what, when — config edits, deletes, batch actions."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)


# Composite index that reporting will lean on (per-transformer, newest first).
Index("ix_units_tf_started", Unit.transformer_id, Unit.started_at.desc())

"""SQLAlchemy implementation of the system-wide pattern-library port."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from evorove_lead.pattern_library import (
    NullPatternLibrary,
    PatternLibrary,
    PatternObservation,
    close_rate_band,
    new_pattern_id,
)
from evorove_lead.sqlalchemy_models import HypothesisPatternLibraryRow

_BAND_RANK = {"high": 0, "medium": 1, "low": 2}


def _to_observation(row: HypothesisPatternLibraryRow) -> PatternObservation:
    return PatternObservation(
        business_archetype=row.business_archetype,
        channel_family=row.channel_family,
        query_pattern=row.query_pattern,
        observed_close_rate_band=row.observed_close_rate_band,
        sample_size=row.sample_size,
        updated_at=row.updated_at,
    )


class SqlAlchemyPatternLibrary:
    """System-wide: no `business_id` anywhere in this class."""

    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

    def record_observation(
        self,
        *,
        business_archetype: str,
        channel_family: str,
        query_pattern: str,
        close_rate: float,
        sample_size: int,
    ) -> None:
        band = close_rate_band(close_rate)
        with self._session_factory() as session:
            existing = session.scalar(
                select(HypothesisPatternLibraryRow).where(
                    HypothesisPatternLibraryRow.business_archetype == business_archetype,
                    HypothesisPatternLibraryRow.channel_family == channel_family,
                    HypothesisPatternLibraryRow.query_pattern == query_pattern,
                )
            )
            row_id = existing.id if existing is not None else new_pattern_id()
            session.merge(
                HypothesisPatternLibraryRow(
                    id=row_id,
                    business_archetype=business_archetype,
                    channel_family=channel_family,
                    query_pattern=query_pattern,
                    observed_close_rate_band=band,
                    sample_size=sample_size,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

    def suggest_patterns(self, business_archetype: str):
        with self._session_factory() as session:
            rows = session.scalars(
                select(HypothesisPatternLibraryRow).where(
                    HypothesisPatternLibraryRow.business_archetype == business_archetype
                )
            )
            observations = [_to_observation(row) for row in rows]
        return tuple(sorted(observations, key=lambda o: _BAND_RANK[o.observed_close_rate_band]))


def pattern_library_from_env() -> PatternLibrary:
    """Same `DATABASE_URL` as the tenant warehouse -- one system-wide table in it."""

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return NullPatternLibrary()
    return SqlAlchemyPatternLibrary(create_engine(database_url, future=True))

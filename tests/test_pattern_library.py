from sqlalchemy import create_engine

from evorove_lead.pattern_library import (
    NullPatternLibrary,
    RecordingPatternLibrary,
    close_rate_band,
)
from evorove_lead.sqlalchemy_pattern_library import SqlAlchemyPatternLibrary
from evorove_lead.sqlalchemy_warehouse import create_all


def test_close_rate_band_buckets():
    assert close_rate_band(0.0) == "low"
    assert close_rate_band(0.09) == "low"
    assert close_rate_band(0.1) == "medium"
    assert close_rate_band(0.29) == "medium"
    assert close_rate_band(0.3) == "high"
    assert close_rate_band(1.0) == "high"


def test_null_library_suggests_nothing():
    library = NullPatternLibrary()
    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.5,
        sample_size=10,
    )
    assert library.suggest_patterns("barbershop") == ()


def test_recording_library_upserts_and_ranks_by_band():
    library = RecordingPatternLibrary()

    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="demographic_fit",
        close_rate=0.05,
        sample_size=5,
    )
    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.5,
        sample_size=8,
    )
    library.record_observation(
        business_archetype="catering",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.9,
        sample_size=3,
    )

    suggestions = library.suggest_patterns("barbershop")

    assert [s.query_pattern for s in suggestions] == ["need_statement", "demographic_fit"]
    assert suggestions[0].observed_close_rate_band == "high"
    assert suggestions[1].observed_close_rate_band == "low"


def test_recording_library_updates_existing_pattern_not_duplicates():
    library = RecordingPatternLibrary()
    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.05,
        sample_size=2,
    )
    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.5,
        sample_size=9,
    )

    suggestions = library.suggest_patterns("barbershop")

    assert len(suggestions) == 1
    assert suggestions[0].sample_size == 9
    assert suggestions[0].observed_close_rate_band == "high"


def test_sqlalchemy_library_is_system_wide_and_upserts(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'patterns.db'}", future=True)
    create_all(engine)
    library = SqlAlchemyPatternLibrary(engine)

    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.05,
        sample_size=2,
    )
    library.record_observation(
        business_archetype="barbershop",
        channel_family="web_search",
        query_pattern="need_statement",
        close_rate=0.5,
        sample_size=9,
    )
    library.record_observation(
        business_archetype="catering",
        channel_family="web_search",
        query_pattern="public_ask",
        close_rate=0.4,
        sample_size=4,
    )

    barbershop = library.suggest_patterns("barbershop")
    assert len(barbershop) == 1
    assert barbershop[0].sample_size == 9
    assert barbershop[0].observed_close_rate_band == "high"
    assert len(library.suggest_patterns("catering")) == 1
    assert library.suggest_patterns("unknown_archetype") == ()


def test_pattern_library_row_has_no_tenant_or_pii_columns():
    from evorove_lead.sqlalchemy_models import HypothesisPatternLibraryRow

    columns = {c.name for c in HypothesisPatternLibraryRow.__table__.columns}
    assert "business_id" not in columns
    assert columns == {
        "id",
        "business_archetype",
        "channel_family",
        "query_pattern",
        "observed_close_rate_band",
        "sample_size",
        "updated_at",
    }

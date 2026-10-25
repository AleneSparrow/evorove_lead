"""One Alembic line from 0001 to head. Does not open DATABASE_URL."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (
    (None, "0001"),
    ("0001", "0002"),
    ("0002", "0003"),
    ("0003", "0004"),
    ("0004", "0005"),
    ("0005", "0006"),
    ("0006", "0007"),
    ("0007", "0008"),
    ("0008", "0009"),
    ("0009", "0010"),
    ("0010", "0011"),
    ("0011", "0012"),
    ("0012", "0013"),
)


def test_revisions_are_a_single_line_without_duplicate_ids():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    pairs = []
    revision = script.get_current_head()
    seen = set()
    while revision is not None:
        item = script.get_revision(revision)
        assert item.revision not in seen
        seen.add(item.revision)
        down = item.down_revision
        if isinstance(down, tuple):
            raise AssertionError(f"merge revision {item.revision} -> {down}")
        pairs.append((down, item.revision))
        revision = down
    pairs.reverse()
    assert pairs == list(EXPECTED)
    assert script.get_current_head() == "0013"

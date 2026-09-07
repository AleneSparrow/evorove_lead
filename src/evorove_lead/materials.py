"""Named bodies of the business's own words.

Optional files on disk live here. The engine can also pass page text
fetched from the owner's public site. This module does not scrape ads
or read secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKIP_NAMES = frozenset({"README.md", "README.txt", ".gitkeep"})
ALLOWED_SUFFIXES = frozenset({".txt", ".md"})


class MaterialRejected(ValueError):
    """A path is not an allowed owner-deposited material."""


@dataclass(frozen=True)
class DepositedMaterial:
    """A named body of the business's own words."""

    name: str
    body: str


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.")


def load_deposited_materials(root: Path) -> tuple[DepositedMaterial, ...]:
    """Load text the owner deposited. README and secrets are not materials."""

    if not root.is_dir():
        raise MaterialRejected("owner-materials directory is required")

    loaded: list[DepositedMaterial] = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if _is_secret_name(path.name):
            raise MaterialRejected("refusing to load secrets")
        if path.name in SKIP_NAMES or path.name.startswith("."):
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        loaded.append(
            DepositedMaterial(name=path.name, body=path.read_text(encoding="utf-8"))
        )
    return tuple(loaded)

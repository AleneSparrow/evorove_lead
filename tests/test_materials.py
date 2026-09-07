from pathlib import Path

import pytest

from evorove_lead import DepositedMaterial, MaterialRejected, load_deposited_materials


def test_loads_owner_text_and_skips_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# instructions\n", encoding="utf-8")
    (tmp_path / "ad-copy.txt").write_text("Weekend catering for local events.\n", encoding="utf-8")

    materials = load_deposited_materials(tmp_path)

    assert materials == (
        DepositedMaterial(name="ad-copy.txt", body="Weekend catering for local events.\n"),
    )


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MaterialRejected, match="directory is required"):
        load_deposited_materials(tmp_path / "missing")


def test_refuses_to_load_env_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=no\n", encoding="utf-8")

    with pytest.raises(MaterialRejected, match="secrets"):
        load_deposited_materials(tmp_path)


def test_readme_only_is_not_a_deposited_offer(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Put your ad copy here.\n", encoding="utf-8")

    assert load_deposited_materials(tmp_path) == ()

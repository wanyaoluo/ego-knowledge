from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ego_knowledge.core import EgoKnowledge


@pytest.fixture()
def ek_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "EgoKnowledge"


@pytest.fixture()
def fresh_ek(ek_root: Path) -> Iterator[EgoKnowledge]:
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    try:
        yield ek
    finally:
        ek.close()


@pytest.fixture()
def fresh_ek_data_only(tmp_path: Path) -> Path:
    return tmp_path / "data" / "EgoKnowledge"

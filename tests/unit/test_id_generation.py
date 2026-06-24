from __future__ import annotations

import pytest

from ego_knowledge.models import Kind, generate_id, parse_id


def test_generate_id_uses_kind_specific_prefix() -> None:
    prefixes = {
        Kind.SOURCE: "ek_src_",
        Kind.NOTE: "ek_note_",
        Kind.DOSSIER: "ek_dos_",
        Kind.CONCEPT: "ek_con_",
        Kind.DECISION: "ek_dec_",
        Kind.VIEW: "ek_view_",
    }

    for kind, prefix in prefixes.items():
        generated = generate_id(kind)
        assert generated.startswith(prefix)
        parsed_kind, payload = parse_id(generated)
        assert parsed_kind is kind
        assert len(payload) == 26


def test_parse_id_rejects_invalid_structure() -> None:
    with pytest.raises(ValueError, match="Invalid ID structure"):
        parse_id("ek_src")

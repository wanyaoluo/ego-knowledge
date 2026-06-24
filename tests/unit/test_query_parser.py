from __future__ import annotations

import pytest

from ego_knowledge.search import (
    Segment,
    SegmentType,
    _classify,
    _expand_mixed_segments,
    _generate_symbol_variants,
    _split_chunk,
    parse_query,
)


@pytest.mark.parametrize(
    ("query", "expected_types"),
    [
        ("", []),
        ("   ", []),
        ("知识库管理", [SegmentType.CJK]),
        ("SPLADE optimization", [SegmentType.ASCII_WORD, SegmentType.ASCII_WORD]),
        ("C++ retrieval", [SegmentType.SYMBOL_TOKEN, SegmentType.ASCII_WORD]),
        ("BGE-M3 Chinese", [SegmentType.MIXED, SegmentType.ASCII_WORD]),
        ("R^2 optimization", [SegmentType.MIXED, SegmentType.ASCII_WORD]),
        ("AI alignment", [SegmentType.ASCII_SHORT, SegmentType.ASCII_WORD]),
        (
            "OpenAI GPT fine-tuning plan",
            [
                SegmentType.ASCII_WORD,
                SegmentType.ASCII_WORD,
                SegmentType.SYMBOL_TOKEN,
                SegmentType.ASCII_WORD,
            ],
        ),
    ],
)
def test_parse_query_routes_mixed_queries(query: str, expected_types: list[SegmentType]) -> None:
    segments = parse_query(query)

    assert [segment.type for segment in segments] == expected_types


@pytest.mark.parametrize(
    ("token", "expected_type"),
    [
        ("v1.2.3", SegmentType.VERSION),
        ("２０２６", SegmentType.FULLWIDTH),
        ("123", SegmentType.NUMBER),
        ("AI", SegmentType.ASCII_SHORT),
        ("Graph", SegmentType.ASCII_WORD),
        ("测试", SegmentType.CJK),
        ("+++", SegmentType.SYMBOL_TOKEN),
        ("😀", SegmentType.EMOJI),
    ],
)
def test_classify_covers_additional_segment_types(token: str, expected_type: SegmentType) -> None:
    assert _classify(token).type == expected_type


def test_split_chunk_breaks_cjk_and_ascii_boundaries() -> None:
    assert _split_chunk("RAG检索AI") == ["RAG", "检索", "AI"]


def test_split_chunk_covers_simple_boundaries() -> None:
    assert _split_chunk("") == []
    assert _split_chunk("知识库管理") == ["知识库管理"]
    assert _split_chunk("hello") == ["hello"]
    assert _split_chunk("test测试end") == ["test", "测试", "end"]


# ---------------------------------------------------------------------------
# Phase 7.3 – MIXED segment secondary splitting
# ---------------------------------------------------------------------------


class TestExpandMixedSegments:
    """Phase 7.3: MIXED segment expansion at non-alphanumeric boundaries."""

    def test_bge_m3_splits_into_bge_and_m3(self) -> None:
        segments = [Segment(SegmentType.MIXED, "BGE-M3")]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 2
        assert expanded[0].type == SegmentType.ASCII_WORD
        assert expanded[0].text == "BGE"
        assert expanded[1].type == SegmentType.MIXED
        assert expanded[1].text == "M3"

    def test_r2_splits_into_r_and_2(self) -> None:
        segments = [Segment(SegmentType.MIXED, "R^2")]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 2
        assert expanded[0].type == SegmentType.ASCII_SHORT
        assert expanded[0].text == "R"
        assert expanded[1].type == SegmentType.NUMBER
        assert expanded[1].text == "2"

    def test_oauth20_splits_into_oauth2_and_0(self) -> None:
        segments = [Segment(SegmentType.MIXED, "OAuth2.0")]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 2
        assert expanded[0].type == SegmentType.MIXED
        assert expanded[0].text == "OAuth2"
        assert expanded[1].type == SegmentType.NUMBER
        assert expanded[1].text == "0"

    def test_no_split_when_single_alnum_run(self) -> None:
        """Pure MIXED without symbol boundaries stays as-is."""
        segments = [Segment(SegmentType.MIXED, "M3")]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 1
        assert expanded[0].text == "M3"

    def test_non_mixed_segments_pass_through(self) -> None:
        segments = [
            Segment(SegmentType.CJK, "测试"),
            Segment(SegmentType.ASCII_WORD, "test"),
        ]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 2
        assert expanded[0].type == SegmentType.CJK
        assert expanded[1].type == SegmentType.ASCII_WORD

    def test_expansion_increases_segment_count(self) -> None:
        """Sub-segments each get independent segment indices for fusion."""
        segments = [
            Segment(SegmentType.ASCII_WORD, "hello"),
            Segment(SegmentType.MIXED, "BGE-M3"),
            Segment(SegmentType.ASCII_WORD, "world"),
        ]
        expanded = _expand_mixed_segments(segments)
        assert len(expanded) == 4  # hello, BGE, M3, world

    def test_multiple_mixed_segments_keep_order(self) -> None:
        segments = [
            Segment(SegmentType.MIXED, "A1+B2"),
            Segment(SegmentType.MIXED, "C3-D4"),
        ]
        expanded = _expand_mixed_segments(segments)
        assert [segment.text for segment in expanded] == ["A1", "B2", "C3", "D4"]

    def test_empty_input(self) -> None:
        assert _expand_mixed_segments([]) == []


# ---------------------------------------------------------------------------
# Phase 7.3 – SYMBOL_TOKEN symbol-stripped variants
# ---------------------------------------------------------------------------


class TestGenerateSymbolVariants:
    """Phase 7.3: SYMBOL_TOKEN symbol-stripped search variants."""

    def test_fine_tuning_variants(self) -> None:
        variants = _generate_symbol_variants("fine-tuning")
        assert variants == ["fine", "tuning"]

    def test_c_plus_plus_variants(self) -> None:
        variants = _generate_symbol_variants("C++")
        assert variants == ["C"]

    def test_no_variants_for_pure_alnum(self) -> None:
        variants = _generate_symbol_variants("test")
        assert variants == []

    def test_multi_symbol_variants(self) -> None:
        variants = _generate_symbol_variants("R-lang/v2")
        assert variants == ["R", "lang", "v2"]

    def test_no_variants_for_empty(self) -> None:
        variants = _generate_symbol_variants("+++")
        assert variants == []

    def test_empty_string_has_no_variants(self) -> None:
        assert _generate_symbol_variants("") == []

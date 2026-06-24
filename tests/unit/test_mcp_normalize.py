"""Unit coverage for MCP argument normalization of mis-serialized lists."""

from __future__ import annotations

from ego_knowledge.mcp_server._normalize import _coerce_listlike, normalize_mapping


class TestCoerceListlike:
    def test_plain_list_returned_as_is(self) -> None:
        assert _coerce_listlike(["a", "b"]) == ["a", "b"]

    def test_empty_list_returned_as_is(self) -> None:
        assert _coerce_listlike([]) == []

    def test_item_wrapper_restored(self) -> None:
        assert _coerce_listlike({"item": ["a", "b"]}) == ["a", "b"]

    def test_items_wrapper_restored(self) -> None:
        assert _coerce_listlike({"items": ["a", "b"]}) == ["a", "b"]

    def test_wrapper_with_non_list_value_left_untouched(self) -> None:
        assert _coerce_listlike({"item": "scalar"}) == {"item": "scalar"}

    def test_wrapper_with_empty_list_restored(self) -> None:
        assert _coerce_listlike({"item": []}) == []

    def test_wrapper_with_extra_keys_left_untouched(self) -> None:
        # A real mapping must not be mistaken for a wrapped list.
        assert _coerce_listlike({"item": ["a"], "other": 1}) == {"item": ["a"], "other": 1}

    def test_unrelated_single_key_dict_left_untouched(self) -> None:
        assert _coerce_listlike({"name": ["a"]}) == {"name": ["a"]}

    def test_json_array_string_restored(self) -> None:
        assert _coerce_listlike('["a", "b"]') == ["a", "b"]

    def test_empty_json_array_string_restored(self) -> None:
        assert _coerce_listlike("[]") == []

    def test_padded_json_array_string_restored(self) -> None:
        assert _coerce_listlike('  ["a", "b"] ') == ["a", "b"]

    def test_non_array_string_left_untouched(self) -> None:
        assert _coerce_listlike("hello") == "hello"

    def test_bracketed_non_json_string_left_untouched(self) -> None:
        assert _coerce_listlike("[not json") == "[not json"

    def test_json_object_string_left_untouched(self) -> None:
        # Only array-shaped strings are candidates; object strings stay as-is.
        assert _coerce_listlike('{"k": 1}') == '{"k": 1}'

    def test_scalars_left_untouched(self) -> None:
        assert _coerce_listlike(42) == 42
        assert _coerce_listlike(3.14) == 3.14
        assert _coerce_listlike(True) is True
        assert _coerce_listlike(None) is None


class TestNormalizeMapping:
    def test_repairs_wrapped_and_stringified_lists_in_one_payload(self) -> None:
        payload: dict[str, object] = {
            "title": "归一化样本",
            "tags": {"item": ["bug", "mcp"]},
            "search_terms": '["归一化", "normalize"]',
            "source_url": "https://example.com/x",
        }
        assert normalize_mapping(payload) == {
            "title": "归一化样本",
            "tags": ["bug", "mcp"],
            "search_terms": ["归一化", "normalize"],
            "source_url": "https://example.com/x",
        }

    def test_descends_into_nested_mappings_and_lists(self) -> None:
        payload: dict[str, object] = {
            "nested": {"evidence_refs": {"items": ["e1", "e2"]}},
            "matrix": [{"item": ["row"]}],
        }
        assert normalize_mapping(payload) == {
            "nested": {"evidence_refs": ["e1", "e2"]},
            "matrix": [["row"]],
        }

    def test_already_correct_payload_is_unchanged(self) -> None:
        payload: dict[str, object] = {"title": "x", "tags": ["a", "b"]}
        assert normalize_mapping(payload) == payload

    def test_idempotent(self) -> None:
        payload: dict[str, object] = {"tags": {"item": ["a"]}, "deep": {"x": '["y"]'}}
        once = normalize_mapping(payload)
        twice = normalize_mapping(once)
        assert twice == once

    def test_preserves_real_single_key_dicts_that_are_not_wrappers(self) -> None:
        payload: dict[str, object] = {"meta": {"name": ["a", "b"]}}
        assert normalize_mapping(payload) == {"meta": {"name": ["a", "b"]}}

    def test_documents_known_wrapper_heuristic_for_nested_item_mapping(self) -> None:
        payload: dict[str, object] = {"meta": {"item": ["legit_data"]}}

        # Known heuristic: current entry schemas do not allow legitimate nested
        # {"item": [...]} mappings, so this shape is treated as a client list
        # wrapper. If a future schema permits such mappings, revisit _normalize.
        assert normalize_mapping(payload) == {"meta": ["legit_data"]}

    def test_documents_known_json_array_string_heuristic(self) -> None:
        payload: dict[str, object] = {"snippet": "[1, 2, 3]"}

        # Known heuristic: current entry schemas do not define free-form string
        # fields where a JSON array literal is valid business data.
        assert normalize_mapping(payload) == {"snippet": [1, 2, 3]}


class TestCollapsesMultiLayerWrapper:
    """Regression: real clients (observed in session ses_10c6a285) emit multiple
    layers of ``item``/``items`` wrapping, e.g. ``{"item": {"item": [...]}}``.
    A single pass of ``_coerce_listlike`` only peels one layer and leaves the
    outer ``item`` key behind, which then trips schema validation downstream.
    """

    def test_root_mapping_is_preserved_even_if_key_is_item(self) -> None:
        # normalize_mapping promises dict -> dict for MCP payload/changes roots;
        # only field values are repaired as list-like client serialization.
        assert normalize_mapping({"item": {"item": ["a"]}}) == {"item": ["a"]}

    def test_real_field_with_double_item_wrapper(self) -> None:
        # Mirrors the actual failed payload shape on tags/search_terms.
        payload: dict[str, object] = {"tags": {"item": {"item": ["bug", "mcp"]}}}
        assert normalize_mapping(payload) == {"tags": ["bug", "mcp"]}

    def test_mixed_item_items_keys_across_layers(self) -> None:
        payload: dict[str, object] = {"search_terms": {"items": {"item": ["x", "y"]}}}
        assert normalize_mapping(payload) == {"search_terms": ["x", "y"]}

    def test_triple_layer_wrapper_collapses(self) -> None:
        payload: dict[str, object] = {"tags": {"item": {"items": {"item": ["deep"]}}}}
        assert normalize_mapping(payload) == {"tags": ["deep"]}

    def test_multi_field_payload_with_varied_wrap_depths(self) -> None:
        # Simultaneous repair across the four real failure fields at different
        # nesting depths, exactly as observed in the wild session.
        payload: dict[str, object] = {
            "tags": {"item": ["bug", "mcp"]},
            "search_terms": {"item": {"item": ["归一化", "normalize"]}},
            "source_refs": {"items": {"items": ["ek_src_a", "ek_src_b"]}},
            "evidence_refs": {"item": {"items": ["ek_con_c"]}},
        }
        assert normalize_mapping(payload) == {
            "tags": ["bug", "mcp"],
            "search_terms": ["归一化", "normalize"],
            "source_refs": ["ek_src_a", "ek_src_b"],
            "evidence_refs": ["ek_con_c"],
        }

    def test_wrapper_whose_inner_value_is_not_yet_a_list(self) -> None:
        # Outer ``item`` wraps a still-nested mapping that only becomes a list
        # after its own child is normalized; the re-coerce must catch it.
        payload: dict[str, object] = {"meta": {"item": {"item": ["nested"]}}}
        assert normalize_mapping(payload) == {"meta": ["nested"]}

    def test_repeated_normalize_on_collapsed_result_is_stable(self) -> None:
        # Runtime re-ingest: the same payload may pass through normalize_mapping
        # more than once (e.g. an upstream adapter already half-repaired it).
        # Idempotency on the collapsed list must hold.
        payload: dict[str, object] = {"tags": {"item": {"item": ["a", "b"]}}}
        once = normalize_mapping(payload)
        twice = normalize_mapping(once)
        assert once == {"tags": ["a", "b"]}
        assert twice == once

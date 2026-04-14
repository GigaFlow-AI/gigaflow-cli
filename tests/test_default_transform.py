"""Tests for the default Arize Phoenix transform file and _load_default_transform()."""

import importlib.resources

import pytest


class TestLoadDefaultTransform:
    def test_returns_string(self):
        from gigaflow._setup import _load_default_transform
        result = _load_default_transform()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_yaml_version(self):
        from gigaflow._setup import _load_default_transform
        assert "version:" in _load_default_transform()

    def test_arize_transform_yaml_constant_is_populated(self):
        from gigaflow._setup import ARIZE_TRANSFORM_YAML
        assert ARIZE_TRANSFORM_YAML
        assert "primitives:" in ARIZE_TRANSFORM_YAML

    def test_file_accessible_via_importlib(self):
        ref = importlib.resources.files("gigaflow.transforms").joinpath("arize_phoenix.yml")
        assert ref.is_file()


class TestDefaultTransformContent:
    """The built-in YAML must have the filter fields and content mappings that AIF needs."""

    @pytest.fixture(scope="class")
    def raw(self):
        import importlib.resources
        ref = importlib.resources.files("gigaflow.transforms").joinpath("arize_phoenix.yml")
        # Use stdlib tomllib-style manual parse — no pyyaml dep in CLI, so just check strings
        return ref.read_text(encoding="utf-8")

    def test_filter_field_is_span_kind(self, raw):
        """Filters must use the top-level span_kind column, not attributes.span.type."""
        assert "field: span_kind" in raw

    def test_filter_values_use_openinference_conventions(self, raw):
        assert 'value: "LLM"' in raw
        # TOOL and RETRIEVER span_kinds both map to tool_invocation via a list filter
        assert '"TOOL"' in raw
        assert '"RETRIEVER"' in raw
        assert 'value: "AGENT"' in raw

    def test_llm_call_maps_completion(self, raw):
        """AIF reads primitive_data['completion'] to get the LLM response text."""
        assert "completion:" in raw

    def test_tool_invocation_maps_tool_output(self, raw):
        """AIF reads primitive_data['tool_output'] to get the tool result text."""
        assert "tool_output:" in raw

    def test_user_input_maps_content(self, raw):
        """AIF reads primitive_data['content'] to extract the user query."""
        assert "content:" in raw

    def test_no_old_filter_field(self, raw):
        """Old incorrect filter field must not be present."""
        assert "attributes.span.type" not in raw

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


class TestLogfireDefaultTransform:
    """The built-in Pydantic Logfire YAML must classify pydantic-ai spans correctly.

    Background: the logfire datasource receives traces from pydantic-ai's
    logfire integration, which emits LLM completion spans with a name of
    ``chat <model_name>`` (e.g. ``chat gpt-4o``, ``chat fake-gpt-4o-mini``).
    An earlier version of this file said ``value: "model request"``, which
    never matches the actual span names — every llm_call span landed as
    ``primitive_type=NULL`` and every downstream AIF groundedness / tool
    consumption metric collapsed to 0.000 even though atomization ran
    successfully. The regression is silent (no error, no log warning,
    just meaningless metrics) so pin the exact value here.

    Tests read the shipped YAML as raw text — no pyyaml import, keeping
    the CLI's zero-dependency install footprint intact.
    """

    @pytest.fixture(scope="class")
    def raw(self):
        ref = importlib.resources.files("gigaflow.transforms").joinpath("logfire.yml")
        return ref.read_text(encoding="utf-8")

    def test_file_accessible_via_importlib(self):
        ref = importlib.resources.files("gigaflow.transforms").joinpath("logfire.yml")
        assert ref.is_file()

    def test_has_primitives_section(self, raw):
        assert "primitives:" in raw

    def test_user_input_matches_agent_run_and_user_turn_exact(self, raw):
        """pydantic-ai's root agent span is named exactly 'agent run'. Later
        user-simulator turns (e.g. the tau-bench harness) and synthesised
        per-turn spans from the Logfire reader both use 'user turn'. Both
        must classify as user_input via exact match."""
        assert '- "agent run"' in raw
        assert '- "user turn"' in raw
        assert 'mode: "exact"' in raw

    def test_llm_call_uses_chat_prefix(self, raw):
        """pydantic-ai emits LLM completion spans as 'chat <model>' — the
        filter value must be 'chat' with prefix matching to capture
        'chat gpt-4o', 'chat fake-gpt-4o-mini', and every other model
        variant. Regression guard for a previous state where the value
        was 'model request', which silently matched zero real spans."""
        assert 'value: "chat"' in raw

    def test_llm_call_filter_does_not_regress_to_model_request(self, raw):
        """'model request' in the filter value would silently misclassify
        every llm_call span. Enforced separately from the positive check
        because a future edit could accidentally add both strings (e.g.
        in a comment) without breaking the positive assertion but still
        leaving a broken filter behind."""
        import re
        # Match the yaml filter value specifically, not comment text —
        # comments may still mention "model request" as historical context.
        lines = [
            line for line in raw.splitlines()
            if re.match(r'\s*value:\s*"model request"', line)
        ]
        assert not lines, (
            f"logfire.yml contains an llm_call filter value 'model request', "
            f"which does not match any real pydantic-ai span name. Offending "
            f"lines: {lines}"
        )

    def test_tool_invocation_uses_running_tool_prefix(self, raw):
        """pydantic-ai tool spans are named 'running tool: <tool_name>'."""
        assert 'value: "running tool"' in raw

    def test_llm_call_maps_completion(self, raw):
        """AIF reads primitive_data['completion'] to get the LLM response text."""
        assert "completion:" in raw

    def test_llm_call_maps_gen_ai_output_messages(self, raw):
        """Pydantic-ai emits OTel GenAI semantic conventions, not Arize
        Phoenix's openinference attributes. The completion field must
        point at gen_ai.output.messages — not llm.output_messages."""
        assert "gen_ai.output.messages" in raw

    def test_tool_invocation_maps_tool_output(self, raw):
        """AIF reads primitive_data['tool_output'] to get the tool result text."""
        assert "tool_output:" in raw

    def test_user_input_maps_pydantic_ai_user_prompt(self, raw):
        """user_input content path is attributes.pydantic_ai.user_prompt —
        synthesized by the logfire_reader from all_messages when the
        native field is absent (see backend/app/datasources/logfire_reader.py)."""
        assert "pydantic_ai.user_prompt" in raw

    def test_source_is_logfire(self, raw):
        assert 'source: "logfire"' in raw

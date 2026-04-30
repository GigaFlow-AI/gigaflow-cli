"""Unit tests for the cost printers on `gigaflow compute`.

These exercise the pure formatting helpers so regressions in the
fallback expressions (missing keys, None fields, divide-by-zero) don't
sneak through the live-only path.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout

from gigaflow.commands.compute import _print_cost_breakdown, _print_cost_summary


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


class TestPrintCostSummary:
    def test_empty_dict_is_silent(self):
        assert _capture(_print_cost_summary, {}) == ""

    def test_none_is_silent(self):
        assert _capture(_print_cost_summary, None) == ""

    def test_well_formed_payload(self):
        out = _capture(
            _print_cost_summary,
            {
                "total_cost_usd": 0.0428,
                "total_tokens": 14760,
                "total_requests": 37,
            },
        )
        assert "$0.0428" in out
        assert "14,760 tokens" in out
        assert "37 requests" in out

    def test_missing_cost_falls_back_to_zero(self):
        out = _capture(_print_cost_summary, {"total_tokens": 100, "total_requests": 1})
        # total_cost_usd absent → 0.0000 printed, not a KeyError
        assert "$0.0000" in out
        assert "100 tokens" in out


class TestPrintCostBreakdown:
    def test_empty_dict_is_silent(self):
        assert _capture(_print_cost_breakdown, {}) == ""

    def test_no_stages_is_silent(self):
        assert _capture(_print_cost_breakdown, {"total_cost_usd": 1.0}) == ""

    def test_full_payload_renders_sorted_desc(self):
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.0428,
                "total_tokens": 14760,
                "total_requests": 37,
                "pricing_version": "2026-04-13",
                "stages": {
                    "atomization": {
                        "model": "gpt-4o-mini",
                        "total_tokens": 9500,
                        "requests": 22,
                        "cost_usd": 0.0061,
                    },
                    "attribution": {
                        "model": "gpt-4o",
                        "total_tokens": 4580,
                        "requests": 12,
                        "cost_usd": 0.0329,
                    },
                },
            },
        )
        # Attribution (more expensive) must render before atomization.
        assert out.index("attribution") < out.index("atomization")
        assert "gpt-4o" in out
        assert "$0.0329" in out
        assert "pricing: 2026-04-13" in out
        assert "100%" in out

    def test_divide_by_zero_guard_when_total_is_zero(self):
        """pct() must not crash when total_cost_usd is 0 (unknown-model run)."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.0,
                "total_tokens": 100,
                "total_requests": 2,
                "stages": {
                    "atomization": {
                        "model": "unknown",
                        "total_tokens": 100,
                        "requests": 2,
                        "cost_usd": 0.0,
                    },
                },
            },
        )
        # No traceback, renders "  0%" for the single stage and the total row.
        assert "$0.0000" in out
        assert "0%" in out

    def test_missing_pricing_version_is_omitted(self):
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.01,
                "total_tokens": 10,
                "total_requests": 1,
                "stages": {
                    "atomization": {
                        "model": "gpt-4o-mini",
                        "total_tokens": 10,
                        "requests": 1,
                        "cost_usd": 0.01,
                    },
                },
            },
        )
        assert "pricing:" not in out


# ── Malformed payloads ──────────────────────────────────────────────────────
#
# These assert the graceful-degradation contract documented on
# `_print_cost_summary` / `_print_cost_breakdown`: missing keys or None values
# anywhere in the payload should fall back to zero / "unknown" rather than
# crash the output path. Prod runs persisted before the cost-instrumentation
# ship (#143) lack some of these fields, and there is no schema validation
# between the backend and the CLI — so any printer must defend against partial
# shapes rather than KeyError / TypeError.


class TestMalformedTokenUsage:
    # ── Missing top-level keys ────────────────────────────────────────────

    def test_summary_missing_total_requests(self):
        """`total_requests` absent → renders as ``0 requests``, no KeyError."""
        out = _capture(
            _print_cost_summary,
            {"total_cost_usd": 0.01, "total_tokens": 100},
        )
        assert "$0.0100" in out
        assert "100 tokens" in out
        assert "0 requests" in out

    def test_summary_missing_total_tokens(self):
        """`total_tokens` absent → renders as ``0 tokens``, no KeyError."""
        out = _capture(
            _print_cost_summary,
            {"total_cost_usd": 0.01, "total_requests": 3},
        )
        assert "$0.0100" in out
        assert "0 tokens" in out
        assert "3 requests" in out

    def test_summary_only_cost_present(self):
        """Only `total_cost_usd` set — both tokens and requests fall back to 0."""
        out = _capture(_print_cost_summary, {"total_cost_usd": 0.05})
        assert "$0.0500" in out
        assert "0 tokens" in out
        assert "0 requests" in out

    def test_breakdown_missing_stages_key_is_silent(self):
        """No `stages` key at all → no-op, like `stages={}`."""
        assert (
            _capture(
                _print_cost_breakdown,
                {"total_cost_usd": 0.5, "total_tokens": 100, "total_requests": 5},
            )
            == ""
        )

    def test_breakdown_missing_total_requests(self):
        """`total_requests` absent → total row prints ``0 req``, no KeyError."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.01,
                "total_tokens": 10,
                # total_requests intentionally missing
                "stages": {
                    "atomization": {
                        "model": "gpt-4o-mini",
                        "total_tokens": 10,
                        "requests": 1,
                        "cost_usd": 0.01,
                    },
                },
            },
        )
        assert "atomization" in out
        # Total row — total_requests defaulted to 0
        assert "0 req" in out
        assert "$0.0100" in out

    # ── None values nested inside stages ──────────────────────────────────

    def test_breakdown_none_fields_inside_stage(self):
        """Stage with None values for the fields we read renders as zeros/unknown."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.0,
                "total_tokens": 5,
                "total_requests": 1,
                # Issue #226 reproducer: a stage that uses an alternate key
                # schema (input_tokens/output_tokens) and a None alongside it.
                # The printer reads `total_tokens` / `requests` / `cost_usd`
                # / `model`, none of which are present → all default safely.
                "stages": {
                    "atomize": {
                        "input_tokens": None,
                        "output_tokens": 5,
                    },
                },
            },
        )
        # No traceback, stage row is rendered with default fillers.
        assert "atomize" in out
        assert "unknown" in out  # model fell back
        assert "0 tok" in out
        assert "0 req" in out
        assert "$0.0000" in out
        # Divide-by-zero guard kicks in for the per-stage pct.
        assert "0%" in out

    def test_breakdown_explicit_none_for_known_fields(self):
        """Even when the canonical keys are present-but-None, defaults apply."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "total_requests": 0,
                "stages": {
                    "atomization": {
                        "model": None,
                        "total_tokens": None,
                        "requests": None,
                        "cost_usd": None,
                    },
                },
            },
        )
        assert "atomization" in out
        assert "unknown" in out
        assert "0 tok" in out
        assert "0 req" in out
        assert "$0.0000" in out

    def test_breakdown_stage_value_is_none(self):
        """A whole stage entry being None (not a dict) must not crash."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "total_requests": 0,
                "stages": {"atomization": None},
            },
        )
        # `stage or {}` makes this look like an empty stage with all defaults.
        assert "atomization" in out
        assert "unknown" in out
        assert "$0.0000" in out

    def test_breakdown_top_level_none_fields(self):
        """Top-level totals being explicitly None renders as zeros, no crash."""
        out = _capture(
            _print_cost_breakdown,
            {
                "total_cost_usd": None,
                "total_tokens": None,
                "total_requests": None,
                "pricing_version": None,
                "stages": {
                    "atomization": {
                        "model": "gpt-4o-mini",
                        "total_tokens": 10,
                        "requests": 1,
                        "cost_usd": 0.0,
                    },
                },
            },
        )
        # Stage row renders, total row renders with zeros, pricing line
        # is suppressed because `pricing_version` falsy.
        assert "atomization" in out
        assert "$0.0000" in out
        assert "pricing:" not in out

    def test_summary_top_level_none_fields(self):
        """Summary handles explicit None for every total field."""
        out = _capture(
            _print_cost_summary,
            {
                "total_cost_usd": None,
                "total_tokens": None,
                "total_requests": None,
            },
        )
        assert "$0.0000" in out
        assert "0 tokens" in out
        assert "0 requests" in out

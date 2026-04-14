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

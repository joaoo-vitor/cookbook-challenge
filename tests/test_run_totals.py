"""Tests for the run totals: what a run used, summed over its model calls."""

import json

from cookbook.extract.__main__ import _report_totals
from cookbook.llm import LLMResult, summarize_calls


def _call(model="m1", inp=1000, out=200, think=None, seconds=2.0, cached=False, attempts=1, fell_back_from=None):
    return LLMResult("text", model, inp, out, seconds, cached=cached, thinking_tokens=think,
                     attempts=attempts, fell_back_from=fell_back_from)


def test_totals_add_up_over_all_calls_and_models():
    totals = summarize_calls([_call("a", 1000, 200, think=50, seconds=2.0), _call("a", 500, 100, seconds=1.5),
                              _call("b", 800, 300, think=700, seconds=3.0)])
    assert (totals["calls"], totals["input_tokens"], totals["output_tokens"], totals["thinking_tokens"]) == (3, 2300, 600, 750)
    assert totals["call_seconds"] == 6.5
    assert totals["by_model"]["a"] == {"calls": 2, "cached_calls": 0, "input_tokens": 1500, "output_tokens": 300,
                                       "thinking_tokens": 50, "call_seconds": 3.5}
    assert totals["by_model"]["b"]["calls"] == 1 and totals["by_model"]["b"]["thinking_tokens"] == 700


def test_cached_calls_are_counted_but_keep_the_cost_of_a_full_run():
    totals = summarize_calls([_call(cached=True, inp=900), _call(inp=100)])
    assert totals["calls"] == 2 and totals["cached_calls"] == 1
    assert totals["input_tokens"] == 1000  # what the run uses with an empty cache


def test_fallbacks_and_extra_attempts_count_only_fresh_calls():
    totals = summarize_calls([
        _call(attempts=3, fell_back_from="main"),                 # fresh: 2 extra attempts, one fallback
        _call(attempts=3, fell_back_from="main", cached=True),    # cached: nothing happened this time
        _call(attempts=1),
    ])
    assert (totals["fallbacks"], totals["extra_attempts"]) == (1, 2)


def test_missing_token_counts_are_zero_and_no_calls_is_all_zero():
    assert summarize_calls([_call(inp=None, out=None)])["input_tokens"] == 0
    empty = summarize_calls([])
    assert empty["calls"] == 0 and empty["by_model"] == {} and empty["call_seconds"] == 0.0


def test_the_report_prints_the_totals_and_saves_a_manifest(tmp_path, capsys):
    path = tmp_path / "run.json"
    _report_totals([_call("a", 1200, 300, seconds=2.5), _call("a", 800, 100, cached=True, seconds=1.0)], 4.2, path,
                   {"input": "recipe.pdf", "pages": 2, "primary": "gemini/a", "second": None,
                    "unreliable_pages": [], "issues": {"high": 0, "medium": 1, "low": 0}})
    shown = capsys.readouterr().out
    assert "2 call(s), 1 from the cache" in shown and "2,000/400/0" in shown and "4.2 s wall-clock" in shown
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["input"] == "recipe.pdf" and saved["wall_seconds"] == 4.2
    assert saved["calls"]["input_tokens"] == 2000 and saved["issues"]["medium"] == 1

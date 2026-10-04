"""The semantic (response) cache signal must survive the tool loop and be checkable.

Background — three defects found while implementing this, all verified:

1. ``tool_loop._finalize`` did ``result.usage = dict(usage_totals)``, replacing the
   usage dict that carried the cache flag. ``usage_totals`` had no such key, so the
   flag was DISCARDED for every tool-calling run -- and every sweep run uses
   tool calling.
2. The header was never read at all: ``getattr(response, "response_headers", None)``
   is always None, because the installed ``openai`` SDK (2.45.0) has no such field
   on ``ChatCompletion``. So even a perfect accumulator would have recorded False.
3. Nothing exported it, so a hit could not be seen in the CSV or the manifest.

These tests drive the REAL loop with a fake client and canned responses: no
network call is made. The prefix-cache test is deliberate -- a high
``cached_tokens`` ratio (76-81% is normal on this pipeline) must NOT be read as a
semantic hit, or every run would be flagged.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from providers import tool_loop  # noqa: E402
from providers.response_utils import (  # noqa: E402
    build_openai_inference_result,
    extract_semantic_cache,
)

TOOL = str(ROOT / "tools" / "check_semantic_cache.py")


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible client (same shape as tests/test_tool_loop_retry.py)
# ---------------------------------------------------------------------------

class _FakeFunction:
    def __init__(self, name="read_file", arguments="{}"):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, idx=0):
        self.id = f"call-{idx}"
        self.function = _FakeFunction()


class _FakeMessage:
    def __init__(self, tool_calls=None, content=""):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, msg, finish="stop"):
        self.message = msg
        self.finish_reason = finish


class _FakeUsage:
    def __init__(self, prompt=100, completion=5, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion
        self.prompt_tokens_details = type("D", (), {"cached_tokens": cached})()


class _FakeResponse:
    """One HTTP response: a message plus its headers (the cache signal lives here)."""

    def __init__(self, msg, usage=None, headers=None, finish="stop"):
        self.choices = [_FakeChoice(msg, finish)]
        self.usage = usage or _FakeUsage()
        if headers is not None:
            self.response_headers = headers


class _ScriptedCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if not self._script:
            raise AssertionError("fake client exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    """Only implements ``create`` -- no ``with_raw_response``.

    That is deliberate: it exercises the documented fallback in
    ``create_completion_with_headers``, which is also what the other test doubles
    in this suite look like.
    """

    def __init__(self, script):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _ScriptedCompletions(script)


HIT_HEADERS = {"x-omniroute-cache-hit": "true", "x-omniroute-cost-saved": "0.0025"}
MISS_HEADERS = {"x-omniroute-cache-hit": "false"}


def _tool_response(idx=0, headers=None):
    return _FakeResponse(_FakeMessage([_FakeToolCall(idx)]), headers=headers,
                         finish="tool_calls")


def _final_response(text="done", headers=None):
    return _FakeResponse(_FakeMessage(None, content=text), headers=headers)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import evaluation.retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _fast_tool(monkeypatch):
    monkeypatch.setattr(tool_loop, "execute_tool", lambda name, args, role="": "TOOL_OUT")


def _run(client, monkeypatch, *, max_tool_turns=5, role="direct"):
    import evaluation.retry as retry_mod

    for cfg in (tool_loop.Config, retry_mod.Config):
        monkeypatch.setattr(cfg, "MAX_RETRIES", 2)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_BASE", 0.0)
        monkeypatch.setattr(cfg, "RATE_LIMIT_BACKOFF_MAX", 0.0)
        monkeypatch.setattr(cfg, "TOOL_OUTPUT_MAX_CHARS", 2000)
        monkeypatch.setattr(cfg, "ACT_TIMEOUT_SECONDS", 0)
    return tool_loop.run_tool_loop(
        client, model="fake", prompt="fix it", role=role,
        tools=[{"type": "function", "function": {"name": "read_file"}}],
        repo_root=None, max_tool_turns=max_tool_turns,
    )


# ---------------------------------------------------------------------------
# 1. THE regression: the flag must survive the tool loop
# ---------------------------------------------------------------------------

def test_flag_survives_tool_loop(monkeypatch):
    """A cache hit on a TOOL turn must still be in the final usage.

    This is the exact bug: the hit arrives on turn 1's response, then ``_finalize``
    overwrites ``result.usage`` with ``usage_totals``. Before the fix the flag was
    gone from the returned result, so nothing downstream could ever see it.
    """
    client = _FakeClient([
        _tool_response(0, headers=HIT_HEADERS),   # turn 1: HIT, asks for a tool
        _final_response("done", headers=MISS_HEADERS),  # turn 2: final answer
    ])
    result = _run(client, monkeypatch)

    assert result.usage is not None
    assert result.usage.get("semantic_cache_hit") is True, (
        f"the hit on turn 1 must survive _finalize; usage={result.usage}"
    )
    assert result.usage.get("semantic_cache_cost_saved_usd") == pytest.approx(0.0025)


def test_hit_is_sticky_across_later_misses(monkeypatch):
    """A later MISS must not clear an earlier HIT.

    One cached response is enough to make the act unrepresentative, so the flag is
    OR-ed across turns rather than taken from the last response.
    """
    client = _FakeClient([
        _tool_response(0, headers=HIT_HEADERS),
        _tool_response(1, headers=MISS_HEADERS),
        _final_response("done", headers=MISS_HEADERS),
    ])
    result = _run(client, monkeypatch)
    assert result.usage.get("semantic_cache_hit") is True


def test_no_hit_stays_false(monkeypatch):
    """The normal case: no header, or an explicit false, must NOT set the flag."""
    client = _FakeClient([
        _tool_response(0, headers=MISS_HEADERS),
        _final_response("done", headers=None),
    ])
    result = _run(client, monkeypatch)
    assert result.usage.get("semantic_cache_hit") is False
    assert result.usage.get("semantic_cache_cost_saved_usd") == 0.0
    assert result.usage.get("semantic_cache_hit_turns") == 0


def test_hit_turns_counts_every_hitting_request(monkeypatch):
    """The count must distinguish "one request repeated" from "whole act replayed".

    The boolean alone cannot: a single cached turn and a fully replayed act both
    read as True, but they mean very different things for triage.
    """
    client = _FakeClient([
        _tool_response(0, headers=HIT_HEADERS),
        _tool_response(1, headers=MISS_HEADERS),
        _tool_response(2, headers=HIT_HEADERS),
        _final_response("done", headers=HIT_HEADERS),
    ])
    result = _run(client, monkeypatch, max_tool_turns=5)
    assert result.usage.get("semantic_cache_hit") is True
    assert result.usage.get("semantic_cache_hit_turns") == 3, (
        f"3 of 4 requests carried the hit header; usage={result.usage}"
    )
    # Cost saved accumulates across hits (3 x 0.0025), not once.
    assert result.usage.get("semantic_cache_cost_saved_usd") == pytest.approx(0.0075)


def test_hit_turns_zero_when_clean(monkeypatch):
    client = _FakeClient([
        _tool_response(0, headers=MISS_HEADERS),
        _final_response("done", headers=MISS_HEADERS),
    ])
    result = _run(client, monkeypatch)
    assert result.usage.get("semantic_cache_hit_turns") == 0


# ---------------------------------------------------------------------------
# 1b. The context-limit exit path builds its OWN InferenceResult
# ---------------------------------------------------------------------------

class _OverflowError(Exception):
    """Stands in for the provider's context error (the wording is what matters)."""


class _OverflowingCompletions:
    """Normal turns, then a context error -- and an error again on the final call.

    The final-answer request also overflows, which is the case that returns via the
    context-limit branch in ``run_tool_loop``.
    """

    def __init__(self, responses, overflow_after):
        self._responses = list(responses)
        self.overflow_after = overflow_after
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls > self.overflow_after:
            raise _OverflowError("maximum context length exceeded")
        return self._responses.pop(0)


def test_flag_survives_the_context_limit_exit_path(monkeypatch):
    """A HIT followed by a context overflow must still report the hit.

    ``run_tool_loop`` has a SECOND ``usage=dict(usage_totals)`` site on the
    context-limit branch, which builds its own ``InferenceResult``. Both sites must
    carry the flag: an act that hit the cache and then overflowed is still a
    contaminated act, and losing the flag here would hide exactly the run a gate
    needs to catch.
    """
    completions = _OverflowingCompletions(
        responses=[_tool_response(0, headers=HIT_HEADERS)],   # turn 1: HIT
        overflow_after=1,                                     # then overflow forever
    )
    client = _FakeClient.__new__(_FakeClient)
    client.chat = type("Chat", (), {})()
    client.chat.completions = completions

    result = _run(client, monkeypatch, max_tool_turns=3)

    assert result.finish_reason == "context_limit", (
        f"expected the context-limit exit path; got {result.finish_reason!r}"
    )
    assert result.usage.get("semantic_cache_hit") is True, (
        "the context-limit branch dropped the cache flag; usage="
        f"{result.usage}"
    )
    assert result.usage.get("semantic_cache_hit_turns") == 1
    assert result.usage.get("semantic_cache_cost_saved_usd") == pytest.approx(0.0025)


def test_context_limit_without_a_hit_stays_clean(monkeypatch):
    """Same path, no hit header: the flag must stay False (no false positive)."""
    completions = _OverflowingCompletions(
        responses=[_tool_response(0, headers=MISS_HEADERS)],
        overflow_after=1,
    )
    client = _FakeClient.__new__(_FakeClient)
    client.chat = type("Chat", (), {})()
    client.chat.completions = completions

    result = _run(client, monkeypatch, max_tool_turns=3)

    assert result.finish_reason == "context_limit"
    assert result.usage.get("semantic_cache_hit") is False
    assert result.usage.get("semantic_cache_hit_turns") == 0


def test_flag_accumulates_on_the_final_answer_turn(monkeypatch):
    """A hit on the LAST turn (the final answer) must also be recorded."""
    client = _FakeClient([
        _tool_response(0, headers=MISS_HEADERS),
        _final_response("done", headers=HIT_HEADERS),
    ])
    result = _run(client, monkeypatch)
    assert result.usage.get("semantic_cache_hit") is True


# ---------------------------------------------------------------------------
# 2. Prefix cache is NOT a semantic hit (must not be conflated)
# ---------------------------------------------------------------------------

def test_high_prefix_cache_is_not_a_semantic_hit(monkeypatch):
    """76-81% prefix caching is normal here; it must never raise the flag.

    Prefix caching (``cached_tokens``) is a shared-prompt discount. A semantic
    cache hit is an entire replayed response. Reading the first as the second
    would flag every single run of the sweep.
    """
    usage = _FakeUsage(prompt=1000, completion=10, cached=810)   # 81% cached
    client = _FakeClient([
        _FakeResponse(_FakeMessage([_FakeToolCall(0)]), usage=usage,
                      headers=MISS_HEADERS, finish="tool_calls"),
        _FakeResponse(_FakeMessage(None, content="done"), usage=usage,
                      headers=MISS_HEADERS),
    ])
    result = _run(client, monkeypatch)

    assert result.usage.get("cached_tokens") == 1620, "prefix cache still accumulates"
    assert result.usage.get("semantic_cache_hit") is False, (
        "a high prefix-cache ratio must NOT be reported as a semantic cache hit"
    )


def test_build_result_does_not_infer_hit_from_cached_tokens():
    """No header at all + fully-cached prompt => still not a semantic hit."""
    usage = _FakeUsage(prompt=500, completion=5, cached=500)
    resp = _FakeResponse(_FakeMessage(None, content="hi"), usage=usage)
    result = build_openai_inference_result(resp, role="direct", model="m")
    assert result.usage["semantic_cache_hit"] is False
    assert result.usage["cached_tokens"] == 500


# ---------------------------------------------------------------------------
# 3. Header parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {"x-omniroute-cache-hit": "true"},
    {"x-omniroute-cache-hit": "TRUE"},
    {"X-OmniRoute-Cache-Hit": "true"},
    {"X-OMNIROUTE-CACHE-HIT": "True"},
])
def test_hit_header_is_case_insensitive(headers):
    hit, saved = extract_semantic_cache(headers)
    assert hit is True
    assert saved == 0.0


def test_missing_or_false_header_is_not_a_hit():
    assert extract_semantic_cache(None) == (False, 0.0)
    assert extract_semantic_cache({}) == (False, 0.0)
    assert extract_semantic_cache({"x-omniroute-cache-hit": "false"}) == (False, 0.0)


def test_cost_saved_parsed_and_bad_values_tolerated():
    assert extract_semantic_cache(
        {"x-omniroute-cache-hit": "true", "x-omniroute-cost-saved": "0.25"}
    ) == (True, 0.25)
    # A malformed number must not lose the HIT itself -- the hit is the important
    # part; the saved amount is decoration.
    assert extract_semantic_cache(
        {"x-omniroute-cache-hit": "true", "x-omniroute-cost-saved": "not-a-number"}
    ) == (True, 0.0)


def test_httpx_style_headers_object_is_supported():
    """``with_raw_response`` hands back httpx.Headers, not a dict."""
    import httpx

    headers = httpx.Headers({"X-OmniRoute-Cache-Hit": "true",
                             "X-OmniRoute-Cost-Saved": "0.5"})
    assert extract_semantic_cache(headers) == (True, 0.5)


# ---------------------------------------------------------------------------
# 4. Export: the flag must be visible in the CSV and the manifest
# ---------------------------------------------------------------------------

def _experiment_result(hit: bool):
    from models.inference import InferenceResult, InferenceRun
    from models.result import (
        CostSummary, EvaluationResult, ExecutionResult, ExperimentResult,
    )

    inf = InferenceResult(
        role="direct", response="x",
        usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11,
               "cached_tokens": 0,
               "semantic_cache_hit": hit,
               "semantic_cache_cost_saved_usd": 0.01 if hit else 0.0},
    )
    run = InferenceRun(patch="", inferences=[inf])
    cost = CostSummary(
        input_cost_usd=0.0, output_cost_usd=0.0, total_cost_usd=0.0,
        total_cost_idr=0.0, semantic_cache_hit=hit,
        semantic_cache_cost_saved_usd=0.01 if hit else 0.0,
    )
    return ExperimentResult(
        instance_id="django__django-1", strategy="direct", model="m",
        execution=ExecutionResult(run=run), cost=cost,
        evaluation=EvaluationResult(success=True),
    )


def test_csv_export_carries_the_flag():
    from experiments.csv_exporter import flatten_for_csv

    hit_row = flatten_for_csv(_experiment_result(hit=True))
    clean_row = flatten_for_csv(_experiment_result(hit=False))

    assert hit_row["semantic_cache_hit"] is True
    assert clean_row["semantic_cache_hit"] is False
    assert hit_row["semantic_cache_cost_saved_usd"] == pytest.approx(0.01)
    # A clean row must carry an explicit False, not a missing key: an absent column
    # is exactly the state that made the hit undetectable in the first place.
    assert "semantic_cache_hit" in clean_row


def test_cost_aggregate_propagates_the_flag():
    """A hit on ONE inference must mark the whole run's CostSummary."""
    from evaluation.cost import CostCalculator
    from models.inference import InferenceResult

    clean = InferenceResult(role="direct", response="x",
                            usage={"prompt_tokens": 1, "completion_tokens": 1,
                                   "total_tokens": 2, "semantic_cache_hit": False})
    hit = InferenceResult(role="executor", response="y",
                          usage={"prompt_tokens": 1, "completion_tokens": 1,
                                 "total_tokens": 2, "semantic_cache_hit": True,
                                 "semantic_cache_cost_saved_usd": 0.02,
                                 "semantic_cache_hit_turns": 3})

    summary = CostCalculator().aggregate([clean, hit])
    assert summary.semantic_cache_hit is True
    assert summary.semantic_cache_cost_saved_usd == pytest.approx(0.02)
    assert summary.semantic_cache_hit_turns == 3


# ---------------------------------------------------------------------------
# 4b. The REAL SDK path: the tool loop must capture headers end to end
# ---------------------------------------------------------------------------

def test_tool_loop_captures_headers_through_the_real_sdk(monkeypatch):
    """Drive run_tool_loop with a REAL OpenAI client over a mock transport.

    This is the regression that matters most. Every other test here uses a fake
    client whose response objects already carry ``response_headers``; that is
    exactly how the Lapis-2 bug stayed invisible, because the real SDK's
    ChatCompletion has no such field. With a real client the header is only
    reachable through ``with_raw_response``, so this test goes RED the moment
    that path is dropped -- while all the fake-client tests stay green.
    """
    import httpx
    from openai import OpenAI

    body = {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "done"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 5,
                  "total_tokens": 1005},
    }

    def handler(request):
        return httpx.Response(200, json=body, headers=HIT_HEADERS)

    client = OpenAI(api_key="test", base_url="http://test.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = _run(client, monkeypatch)

    assert result.usage.get("semantic_cache_hit") is True, (
        "the tool loop did not read the response-cache header from the real SDK "
        f"response; usage={result.usage}"
    )
    assert result.usage.get("semantic_cache_hit_turns") == 1
    assert result.usage.get("semantic_cache_cost_saved_usd") == pytest.approx(0.0025)


def test_tool_loop_real_sdk_clean_run_is_not_flagged(monkeypatch):
    """Same real path, no hit header: the flag must stay False."""
    import httpx
    from openai import OpenAI

    body = {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "done"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 5,
                  "total_tokens": 1005},
    }

    def handler(request):
        return httpx.Response(200, json=body, headers=MISS_HEADERS)

    client = OpenAI(api_key="test", base_url="http://test.invalid/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = _run(client, monkeypatch)
    assert result.usage.get("semantic_cache_hit") is False
    assert result.usage.get("semantic_cache_hit_turns") == 0


def test_manifest_reports_hits_and_per_run_flag(tmp_path):
    from experiments.observability import build_experiment_manifest

    manifest = build_experiment_manifest(
        experiment_id="EXP-TEST",
        provider_name="fake",
        issues=[],
        strategies=["direct"],
        agents=[],
        output_dir=str(tmp_path),
        results=[_experiment_result(hit=True), _experiment_result(hit=False)],
    )
    assert manifest["summary"]["semantic_cache_hits"] == 1
    assert manifest["results"][0]["semantic_cache_hit"] is True
    assert manifest["results"][1]["semantic_cache_hit"] is False


# ---------------------------------------------------------------------------
# 5. The gate tool
# ---------------------------------------------------------------------------

def _write_exp(root: Path, name: str, rows: list[dict]) -> Path:
    exp = root / name
    exp.mkdir(parents=True)
    fieldnames = ["instance_id", "strategy", "semantic_cache_hit",
                  "semantic_cache_cost_saved_usd"]
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return exp


def _run_tool(*args):
    return subprocess.run(
        [sys.executable, TOOL, *args],
        capture_output=True, text=True, cwd=str(ROOT), encoding="utf-8",
    )


def test_checker_exits_zero_on_clean_experiment(tmp_path):
    _write_exp(tmp_path, "EXP-CLEAN", [
        {"instance_id": "i1", "strategy": "direct", "semantic_cache_hit": "False",
         "semantic_cache_cost_saved_usd": "0.0"},
        {"instance_id": "i1", "strategy": "planning", "semantic_cache_hit": "False",
         "semantic_cache_cost_saved_usd": "0.0"},
    ])
    proc = _run_tool("--exp", "EXP-CLEAN", "--results-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no semantic-cache hit" in proc.stdout.lower()


def test_checker_exits_nonzero_on_hit_and_names_the_run(tmp_path):
    _write_exp(tmp_path, "EXP-DIRTY", [
        {"instance_id": "django__django-10914", "strategy": "direct",
         "semantic_cache_hit": "True", "semantic_cache_cost_saved_usd": "0.01"},
        {"instance_id": "django__django-1", "strategy": "planning",
         "semantic_cache_hit": "False", "semantic_cache_cost_saved_usd": "0.0"},
    ])
    proc = _run_tool("--exp", "EXP-DIRTY", "--results-dir", str(tmp_path))
    assert proc.returncode != 0, "a hit MUST make the gate fail"
    assert "django__django-10914" in proc.stdout
    assert "direct" in proc.stdout


def test_checker_flags_high_prefix_cache_as_clean(tmp_path):
    """The gate must read the FLAG, never the cached-token ratio.

    Two rows, both with NO hit flag:
      * 81% cached  -- the normal case measured on batch-1
      * 100% cached -- the AMBIGUOUS case. A semantic hit promotes cached_tokens to
        the full prompt (response_utils), so ``cached == prompt`` looks identical to
        a fully prefix-cached call. A gate that decided by ratio would fire here and
        would fire on every long run -- then be ignored.
    Both must stay clean, because neither carries the header flag.
    """
    exp = tmp_path / "EXP-PREFIX"
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "input_tokens_total",
                           "input_tokens_cached", "semantic_cache_hit"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "input_tokens_total": "1000", "input_tokens_cached": "810",
                         "semantic_cache_hit": "False"})
        writer.writerow({"instance_id": "i2", "strategy": "planning",
                         "input_tokens_total": "1000", "input_tokens_cached": "1000",
                         "semantic_cache_hit": "False"})
    proc = _run_tool("--exp", "EXP-PREFIX", "--results-dir", str(tmp_path))
    assert proc.returncode == 0, (
        "prefix caching (81% AND 100%) must not be reported as a semantic-cache "
        "hit -- only the flag counts: " + proc.stdout
    )


def test_checker_survives_corrupt_and_missing_files(tmp_path):
    """A gate that crashes on a half-written artefact gets bypassed."""
    broken = tmp_path / "EXP-BROKEN"
    broken.mkdir(parents=True)
    (broken / "generation_result.csv").write_text("not,a,valid\ncsv\x00\xff", encoding="utf-8")
    (broken / "manifest.json").write_text("{ this is not json", encoding="utf-8")

    proc = _run_tool("--exp", "EXP-BROKEN", "--results-dir", str(tmp_path))
    assert proc.returncode in (0, 1), (
        "a corrupt file must be reported, not crash the checker: "
        + proc.stdout + proc.stderr
    )
    assert "Traceback" not in proc.stderr

    # A missing experiment must also be handled, not raised.
    proc2 = _run_tool("--exp", "EXP-DOES-NOT-EXIST", "--results-dir", str(tmp_path))
    assert "Traceback" not in proc2.stderr


def test_checker_says_unverifiable_when_flag_absent(tmp_path):
    """Pre-fix artifacts must NOT be reported as clean -- no flag, no verdict."""
    exp = tmp_path / "EXP-PREFIXONLY"
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["instance_id", "strategy",
                                               "input_tokens_total"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "input_tokens_total": "1000"})
    proc = _run_tool("--exp", "EXP-PREFIXONLY", "--results-dir", str(tmp_path))
    assert proc.returncode == 0
    assert "UNVERIFIABLE" in proc.stdout, (
        "an experiment without the flag cannot be called clean; got: " + proc.stdout
    )


def test_checker_all_scan_finds_a_hit_among_many(tmp_path):
    _write_exp(tmp_path, "EXP-A", [
        {"instance_id": "i1", "strategy": "direct", "semantic_cache_hit": "False",
         "semantic_cache_cost_saved_usd": "0.0"}])
    _write_exp(tmp_path, "EXP-B", [
        {"instance_id": "i2", "strategy": "review", "semantic_cache_hit": "true",
         "semantic_cache_cost_saved_usd": "0.03"}])
    proc = _run_tool("--all", "--results-dir", str(tmp_path), "--quiet")
    assert proc.returncode == 1
    assert "i2" in proc.stdout


def test_checker_reads_the_manifest_when_the_csv_is_gone(tmp_path):
    exp = tmp_path / "EXP-MANIFESTONLY"
    exp.mkdir(parents=True)
    (exp / "manifest.json").write_text(json.dumps({
        "summary": {"semantic_cache_hits": 1},
        "results": [{"instance_id": "i9", "strategy": "planning",
                     "semantic_cache_hit": True,
                     "semantic_cache_cost_saved_usd": 0.05}],
    }), encoding="utf-8")
    proc = _run_tool("--exp", "EXP-MANIFESTONLY", "--results-dir", str(tmp_path))
    assert proc.returncode == 1
    assert "i9" in proc.stdout


# ---------------------------------------------------------------------------
# 5b. The core honesty rule: a BLANK cell means "unknown", not "clean"
# ---------------------------------------------------------------------------
# This is the rule the whole gate's credibility rests on. A CSV with the flag
# column present but EMPTY cannot testify either way -- it was written before the
# signal was recorded, or by the savepoint-recovery path. Reporting that as "0
# hits, all clear" would be a false assurance, exactly like reading a missing cost
# as 0.00. The gate must say UNVERIFIABLE instead.

def _write_blank_flag_exp(root: Path, name: str) -> Path:
    """A CSV that HAS the column but leaves every cell empty."""
    exp = root / name
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "input_tokens_total",
                           "semantic_cache_hit", "semantic_cache_hit_turns"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "input_tokens_total": "1000", "semantic_cache_hit": "",
                         "semantic_cache_hit_turns": ""})
        writer.writerow({"instance_id": "i2", "strategy": "planning",
                         "input_tokens_total": "1000", "semantic_cache_hit": "",
                         "semantic_cache_hit_turns": ""})
    return exp


def test_checker_reports_unverifiable_for_blank_flag_cells(tmp_path):
    """Blank cells -> UNVERIFIABLE, NOT a clean verdict."""
    _write_blank_flag_exp(tmp_path, "EXP-BLANK")
    proc = _run_tool("--exp", "EXP-BLANK", "--results-dir", str(tmp_path))

    assert "UNVERIFIABLE" in proc.stdout, (
        "a blank flag must be reported as unverifiable; calling it clean is a "
        "false assurance. got: " + proc.stdout
    )
    assert proc.returncode == 0, "unknown is not a hit, so the gate must not fail"
    assert "absence of evidence" in proc.stdout.lower(), (
        "the summary must say the verdict is an absence of evidence, not a clean "
        "bill of health; got: " + proc.stdout
    )


def test_checker_blank_flag_is_not_reported_as_zero_hits_clean(tmp_path):
    """The wording matters: it must not claim the experiment was checked and clean."""
    _write_blank_flag_exp(tmp_path, "EXP-BLANK2")
    proc = _run_tool("--exp", "EXP-BLANK2", "--results-dir", str(tmp_path))

    assert "no semantic-cache hit detected" not in proc.stdout.lower(), (
        "a blank flag must NOT produce the clean-verdict wording; got: "
        + proc.stdout
    )


def test_checker_quiet_mode_labels_blank_as_unverifiable(tmp_path):
    """--quiet is used for full-sweep scans, so its one-line label must be honest."""
    _write_blank_flag_exp(tmp_path, "EXP-BLANK3")
    proc = _run_tool("--exp", "EXP-BLANK3", "--results-dir", str(tmp_path), "--quiet")

    assert "UNVERIFIABLE" in proc.stdout, (
        "--quiet summarised a blank-flag experiment without the unverifiable "
        "marker; a batch scan would look clean. got: " + proc.stdout
    )
    assert "clean" not in proc.stdout.lower()


def test_checker_true_flag_exits_nonzero_even_with_other_blanks(tmp_path):
    """A real hit must still fail the gate when other rows are blank."""
    exp = tmp_path / "EXP-MIXED"
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "semantic_cache_hit",
                           "semantic_cache_hit_turns"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "semantic_cache_hit": "", "semantic_cache_hit_turns": ""})
        writer.writerow({"instance_id": "i2", "strategy": "planning",
                         "semantic_cache_hit": "True",
                         "semantic_cache_hit_turns": "4"})
    proc = _run_tool("--exp", "EXP-MIXED", "--results-dir", str(tmp_path))

    assert proc.returncode == 1, (
        "a True flag must fail the gate regardless of blank rows; got exit "
        f"{proc.returncode}: {proc.stdout}"
    )
    assert "i2" in proc.stdout
    assert "4 request(s) hit" in proc.stdout, (
        "the hit count should be reported so one repeat can be told from a full "
        "replay; got: " + proc.stdout
    )


def test_checker_blank_cell_does_not_count_as_a_hit(tmp_path):
    """Blank must not be coerced to True (bool('') is False, but be explicit)."""
    _write_blank_flag_exp(tmp_path, "EXP-BLANK4")
    proc = _run_tool("--exp", "EXP-BLANK4", "--results-dir", str(tmp_path))
    assert proc.returncode == 0, "a blank cell is not a hit"
    assert "SEMANTIC CACHE HITS" not in proc.stdout


# ---------------------------------------------------------------------------
# 5c. One run is ONE hit, even when it appears in several artefacts
# ---------------------------------------------------------------------------
# Every experiment has BOTH a generation CSV and a manifest, and the same run is
# recorded in each. Concatenating the two sources counts it twice, so the headline
# read ~2x the truth across 30 experiments. The exit code stayed correct, which is
# why it went unnoticed -- the GATE was fine and the NUMBER was wrong, and the
# number is what gets quoted. This project already hit this class once
# (resolved/total > 100% from duplicate rows).

def _write_exp_with_manifest(root: Path, name: str, hit: bool,
                             turns: int = 1, saved: str = "0.01") -> Path:
    """One experiment where the SAME run is in both the CSV and the manifest."""
    exp = root / name
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "semantic_cache_hit",
                           "semantic_cache_hit_turns"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "semantic_cache_hit": "True" if hit else "False",
                         "semantic_cache_hit_turns": str(turns) if hit else "0"})
    (exp / "manifest.json").write_text(json.dumps({
        "summary": {"semantic_cache_hits": 1 if hit else 0},
        "results": [{"instance_id": "i1", "strategy": "direct",
                     "semantic_cache_hit": hit,
                     "semantic_cache_hit_turns": turns if hit else 0,
                     "semantic_cache_cost_saved_usd": saved if hit else "0.0"}],
    }), encoding="utf-8")
    return exp


def test_same_run_in_csv_and_manifest_counts_once(tmp_path):
    """A run recorded in BOTH artefacts must be ONE hit, not two.

    This is the regression for the double-count: the same contaminated run appears
    in generation_result.csv and manifest.json, so a naive concatenation reports
    "SEMANTIC CACHE HITS: 2" and prints the run twice.
    """
    _write_exp_with_manifest(tmp_path, "EXP-DUP", hit=True)
    proc = _run_tool("--exp", "EXP-DUP", "--results-dir", str(tmp_path))

    assert "SEMANTIC CACHE HITS: 1" in proc.stdout, (
        "one run present in the CSV and the manifest was counted more than once; "
        "the headline number would be ~2x the truth. got: " + proc.stdout
    )
    assert "SEMANTIC CACHE HITS: 2" not in proc.stdout
    assert proc.stdout.count("i1 / direct") == 1, (
        "the run was listed more than once; got: " + proc.stdout
    )
    assert proc.returncode == 1, "the gate must still fail"


def test_dedup_merges_fields_from_both_sources(tmp_path):
    """Dedup must not throw away a value only one source carries.

    The CSV has the hit count, the manifest has the saved cost. Merging them keeps
    both; dropping the duplicate outright would lose one of the two numbers.
    """
    exp = tmp_path / "EXP-MERGE"
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "semantic_cache_hit",
                           "semantic_cache_hit_turns"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "semantic_cache_hit": "True",
                         "semantic_cache_hit_turns": "7"})
    (exp / "manifest.json").write_text(json.dumps({
        "summary": {"semantic_cache_hits": 1},
        "results": [{"instance_id": "i1", "strategy": "direct",
                     "semantic_cache_hit": True,
                     "semantic_cache_cost_saved_usd": 0.42}],
    }), encoding="utf-8")

    proc = _run_tool("--exp", "EXP-MERGE", "--results-dir", str(tmp_path))

    assert "SEMANTIC CACHE HITS: 1" in proc.stdout
    assert "7 request(s) hit" in proc.stdout, (
        "the hit count from the CSV was lost during the merge; got: " + proc.stdout
    )
    assert "$0.42" in proc.stdout, (
        "the saved cost from the manifest was lost during the merge; got: "
        + proc.stdout
    )


def test_two_distinct_runs_still_count_as_two(tmp_path):
    """Dedup must not collapse DIFFERENT runs that share an instance or strategy.

    The key is (instance_id, strategy): same instance with a different strategy, or
    the same strategy on a different instance, are separate runs and must both be
    reported. An over-eager dedup (say, by instance only) would hide a real hit.
    """
    exp = tmp_path / "EXP-TWO"
    exp.mkdir(parents=True)
    with (exp / "generation_result.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["instance_id", "strategy", "semantic_cache_hit"])
        writer.writeheader()
        writer.writerow({"instance_id": "i1", "strategy": "direct",
                         "semantic_cache_hit": "True"})
        writer.writerow({"instance_id": "i1", "strategy": "planning",
                         "semantic_cache_hit": "True"})
        writer.writerow({"instance_id": "i2", "strategy": "direct",
                         "semantic_cache_hit": "True"})
    proc = _run_tool("--exp", "EXP-TWO", "--results-dir", str(tmp_path))

    assert "SEMANTIC CACHE HITS: 3" in proc.stdout, (
        "three distinct (instance, strategy) runs must count as three; got: "
        + proc.stdout
    )


def test_same_experiment_is_scanned_once(tmp_path):
    """--all plus an explicit --exp naming a covered experiment must scan it once.

    Otherwise the summary reports the experiment twice and the hit total doubles --
    the same double-count one level up. Passing the same --exp twice must also be
    idempotent.
    """
    _write_exp_with_manifest(tmp_path, "EXP-ONCE", hit=True)

    proc = _run_tool("--all", "--exp", "EXP-ONCE", "--results-dir", str(tmp_path))
    assert "Scanned 1 experiment(s); 1 semantic-cache hit(s)." in proc.stdout, (
        "--all plus --exp scanned the same experiment twice; got: " + proc.stdout
    )

    proc2 = _run_tool("--exp", "EXP-ONCE", "--exp", "EXP-ONCE",
                      "--results-dir", str(tmp_path))
    assert "Scanned 1 experiment(s); 1 semantic-cache hit(s)." in proc2.stdout, (
        "the same --exp given twice was counted twice; got: " + proc2.stdout
    )

"""Offline unit tests for benchmark tooling, OpenAICompatibleClient, and extended Metrics."""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from astverifier.baselines.llm import (  # noqa: E402
    LLMClient,
    OpenAICompatibleClient,
    RateLimitedClient,
    StubClient,
)
from astverifier.shared.metrics import aggregate, resolve_pricing  # noqa: E402
from astverifier.shared.schemas import (  # noqa: E402
    AnchorInfo,
    ClaimResult,
    CounterexampleInfo,
    ProgramResult,
    StatusEnum,
)


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_openai_compatible_client_parses_chat_completion(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0.0):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeHTTPResponse(
            {
                "model": "qwen2.5-coder:7b",
                "choices": [
                    {"message": {"role": "assistant", "content": '{"verdict": "VERIFIED"}'}}
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 35},
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:11434/v1/",
        model="qwen2.5-coder:7b",
        api_key="test-key",
        timeout=30.0,
        seed=7,
        max_tokens=512,
        reasoning_effort="none",
    )
    assert isinstance(client, LLMClient)
    reply = client.complete("Check assert x > 0", system="Be strict.", temperature=0.1)

    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "qwen2.5-coder:7b"
    assert captured["body"]["temperature"] == 0.1
    assert captured["body"]["seed"] == 7
    assert captured["body"]["max_tokens"] == 512
    assert captured["body"]["reasoning_effort"] == "none"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "Be strict."}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "Check assert x > 0"}
    assert reply.tokens_in == 120
    assert reply.tokens_out == 35
    assert reply.json() == {"verdict": "VERIFIED"}

    # Optional fields omitted when None
    client_minimal = OpenAICompatibleClient(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5-coder:7b",
        max_tokens=None,
        reasoning_effort=None,
    )
    client_minimal.complete("ping")
    assert "max_tokens" not in captured["body"]
    assert "reasoning_effort" not in captured["body"]


def test_rate_limited_client_delegates():
    stub = StubClient('{"verdict": "VERIFIED"}')
    wrapped = RateLimitedClient(stub, rpm=6000.0)
    assert isinstance(wrapped, LLMClient)
    reply = wrapped.complete("hello")
    assert reply.json()["verdict"] == "VERIFIED"
    assert len(stub.calls) == 1

    with pytest.raises(ValueError):
        RateLimitedClient(stub, rpm=0)


def test_extended_metrics_pricing_and_counts():
    r1 = ProgramResult(
        program_id="loops/p1.py",
        source_path="loops/p1.py",
        n_claims=1,
        n_verified=1,
        claim_results=[
            ClaimResult(
                claim_id="c1",
                claim_text="x >= 0",
                status=StatusEnum.VERIFIED,
                anchor=AnchorInfo(node_ids=["n1"], source_lines=[3], has_grounding=True),
            )
        ],
        elapsed_seconds=0.4,
        total_tokens_in=1000,
        total_tokens_out=500,
        baseline="PROPOSED",
    )
    r2 = ProgramResult(
        program_id="loops/p2.py",
        source_path="loops/p2.py",
        n_claims=1,
        n_counterexample=1,
        claim_results=[
            ClaimResult(
                claim_id="c2",
                claim_text="x > 0",
                status=StatusEnum.COUNTEREXAMPLE,
                anchor=AnchorInfo(node_ids=["n2"], source_lines=[3], has_grounding=True),
                counterexample=CounterexampleInfo(z3_model={"x": 0}, replay_verdict=True),
            )
        ],
        elapsed_seconds=0.6,
        total_tokens_in=2000,
        total_tokens_out=500,
        baseline="PROPOSED",
    )
    gt = {"loops/p1.py": "VERIFIED", "loops/p2.py": "COUNTEREXAMPLE"}
    p_in, p_out = resolve_pricing("gemini-3.5-flash-lite")
    m = aggregate([r1, r2], gt, price_in_per_1m=p_in, price_out_per_1m=p_out)

    assert m.accuracy == 1.0
    assert m.fdr == 0.0
    assert m.crvr == 1.0
    assert m.crvr_confirmed == 1
    assert m.crvr_total == 1
    assert m.hr == 0.0
    assert m.n_verified == 1
    assert m.n_counterexample == 1
    assert m.total_tokens_in == 3000
    assert m.total_tokens_out == 1000
    assert m.latency_avg_s == pytest.approx(0.5)
    assert m.total_cost_usd == pytest.approx((3000 * 0.30 + 1000 * 2.50) / 1_000_000)

    assert resolve_pricing("qwen2.5-coder:7b", provider="ollama") == (0.0, 0.0)


def test_run_benchmark_helpers_and_checkpoint_resume(tmp_path: Path, monkeypatch):
    import run_benchmark

    assert run_benchmark.expand_methods("offline") == [
        "PROPOSED",
        "PROPOSED_NO_INV",
        "J",
        "K",
        "L",
    ]
    assert run_benchmark.expand_methods("d,PROPOSED,d") == ["D", "PROPOSED"]

    with pytest.raises(ValueError):
        run_benchmark.expand_methods("NO_SUCH_METHOD")

    # Checkpoint resume skips valid IDs and ignores corrupt trailing lines
    ckpt = tmp_path / "PROPOSED.jsonl"
    ckpt.write_text(
        '{"program_id": "loops/p1.py"}\n{corrupt_json\n',
        encoding="utf-8",
    )
    done = run_benchmark.load_completed_ids(ckpt)
    assert done == {"loops/p1.py"}

    # .env loading respects existing environment variables
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        'TOKENLAB_API_KEY="abc-123"\n'
        "TOKENLAB_BASE_URL=https://tokenlab.example/v1\n"
        "TOKENLAB_MODEL=Qwen3.8-27B\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TOKENLAB_API_KEY", raising=False)
    monkeypatch.delenv("TOKENLAB_BASE_URL", raising=False)
    monkeypatch.setenv("TOKENLAB_MODEL", "preexisting-model")
    loaded = run_benchmark.load_dotenv(env_file)
    assert loaded["TOKENLAB_API_KEY"] == "abc-123"
    assert os.environ["TOKENLAB_BASE_URL"] == "https://tokenlab.example/v1"
    assert os.environ["TOKENLAB_MODEL"] == "preexisting-model"

    # Auto-defaults reasoning_effort='none' for Qwen on Token Lab
    monkeypatch.setenv("TOKENLAB_BASE_URL", "https://api.tokenlab.amd.com/v1")
    monkeypatch.setenv("TOKENLAB_MODEL", "Qwen3.8-27B")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("TOKENLAB_REASONING_EFFORT", raising=False)
    parsed = run_benchmark.build_parser().parse_args(["--provider", "openai"])
    built_client, built_model = run_benchmark.build_llm_client(parsed)
    assert built_model == "Qwen3.8-27B"
    assert isinstance(built_client, OpenAICompatibleClient)
    assert built_client.reasoning_effort == "none"
    assert built_client.max_tokens == 1024

    # PROPOSED_LLM works with any LLMClient (including OpenAI-compatible / Stub)
    stub = StubClient(
        json.dumps(
            {
                "cot_trace": "y is x + 1, so y > x at line 3",
                "claims": [
                    {"line": 3, "expression": "y > x", "rationale": "follows from y = x + 1"},
                    {"line": 3, "expression": "ghost_var > 0", "rationale": "hallucinated var"},
                ],
            }
        )
    )
    src = "def f(x: int) -> int:\n    y = x + 1\n    assert y > x\n    return y\n"
    res = run_benchmark._run_proposed_llm_with_client("p_test.py", src, stub)
    assert res.baseline == "PROPOSED_LLM"
    assert res.n_verified >= 1
    # Hallucinated variable must be caught by AST-Anchor Gate -> counted in HR
    assert res.n_unsupported == 1
    assert res.total_tokens_in > 0

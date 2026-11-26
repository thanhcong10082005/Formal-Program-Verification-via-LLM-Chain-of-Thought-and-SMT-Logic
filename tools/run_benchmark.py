#!/usr/bin/env python3
"""Unified benchmark runner with Gemini / OpenAI-compatible / Ollama support
and per-program JSONL checkpointing & resume.

Reads ``.env`` from the repository root automatically (without overriding
already-exported shell variables), supporting:

* ``GEMINI_API_KEY`` (for ``--provider gemini``)
* ``OPENAI_API_KEY`` / ``TOKENLAB_API_KEY``
* ``OPENAI_BASE_URL`` / ``TOKENLAB_BASE_URL``
* ``OPENAI_MODEL`` / ``TOKENLAB_MODEL``
* ``OPENAI_REASONING_EFFORT`` / ``TOKENLAB_REASONING_EFFORT``
* ``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL``

Examples
--------
1. Full 1,000-sample $0 offline benchmark (PROPOSED, PROPOSED_NO_INV, J, K, L):
    python tools/run_benchmark.py --methods offline --workers 6

2. Stratified 200-sample run on OpenAI-compatible API (e.g. AMD Token Lab via .env):
    python tools/run_benchmark.py --methods PROPOSED_LLM,A,D,E,M,N \\
        --stratified 200 --provider openai --rpm 18 --out-dir results_llm_200

3. Gemini API run with RPM throttling and automatic resume:
    python tools/run_benchmark.py --methods D,E --stratified 200 \\
        --provider gemini --model gemini-3.5-flash-lite --rpm 12
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from astverifier.baselines import (  # noqa: E402
    EXPECTED_IDS,
    GeminiClient,
    LLMClient,
    OpenAICompatibleClient,
    ProgramSpec,
    RateLimitedClient,
    StubClient,
    get_baseline,
)
from astverifier.core.invariants import LoopInvariantGenerator  # noqa: E402
from astverifier.core.pipeline import (  # noqa: E402
    BASELINE_AST_ANCHORED,
    run_pipeline,
)
from astverifier.llm.gemini import (  # noqa: E402
    SYSTEM_PROMPT as COT_SYSTEM_PROMPT,
)
from astverifier.llm.gemini import (  # noqa: E402
    LLMVerificationPlan,
)
from astverifier.shared.claims import (  # noqa: E402
    Claim,
    load_claim_pool,
    load_ground_truth,
)
from astverifier.shared.schemas import (  # noqa: E402
    AnchorInfo,
    ClaimResult,
    ProgramResult,
    StatusEnum,
)

# Shorthand method groups
OFFLINE_DEFAULT_METHODS: tuple[str, ...] = (
    "PROPOSED",
    "PROPOSED_NO_INV",
    "J",
    "K",
    "L",
)
ABLATION_METHODS: tuple[str, ...] = (
    "PROPOSED",
    "PROPOSED_NO_INV",
    "PROPOSED_NO_REPLAY",
)
LLM_BASELINE_METHODS: tuple[str, ...] = tuple(
    bid for bid in EXPECTED_IDS if bid not in ("J", "K", "L")
)
OFFLINE_METHOD_SET: frozenset[str] = frozenset(
    {"PROPOSED", "PROPOSED_NO_INV", "PROPOSED_NO_REPLAY", "J", "K", "L"}
)
PROPOSED_METHODS: frozenset[str] = frozenset(
    {"PROPOSED", "PROPOSED_NO_INV", "PROPOSED_NO_REPLAY", "PROPOSED_LLM"}
)
VALID_METHODS: frozenset[str] = PROPOSED_METHODS | frozenset(EXPECTED_IDS)


def load_dotenv(env_path: Path | None = None) -> dict[str, str]:
    """Load simple ``KEY=VALUE`` pairs from ``.env`` into ``os.environ``.

    Existing environment variables are never overwritten.
    """
    path = env_path or (ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            loaded[key] = val
    return loaded


def expand_methods(spec: str) -> list[str]:
    """Expand a comma-separated list of method IDs and group aliases."""
    aliases: dict[str, tuple[str, ...]] = {
        "offline": OFFLINE_DEFAULT_METHODS,
        "ablation": ABLATION_METHODS,
        "llm": LLM_BASELINE_METHODS,
        "all": (*ABLATION_METHODS, *EXPECTED_IDS),
    }
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        low = token.lower()
        expanded = aliases[low] if low in aliases else (token.upper(),)
        for method in expanded:
            if method not in VALID_METHODS:
                raise ValueError(
                    f"Unknown method {method!r}. Valid: {sorted(VALID_METHODS)} "
                    f"or aliases {sorted(aliases)}"
                )
            if method not in seen:
                seen.add(method)
                ordered.append(method)
    if not ordered:
        raise ValueError("No benchmark methods selected.")
    return ordered


def select_programs(
    manifest_programs: list[dict[str, Any]],
    claim_pool: dict[str, list[Claim]],
    *,
    limit: int = 0,
    stratified: int = 0,
    seed: int = 42,
) -> list[str]:
    """Select program IDs from the manifest in a deterministic order."""
    all_ids = [
        p["path"]
        for p in manifest_programs
        if p.get("path") in claim_pool and claim_pool[p["path"]]
    ]

    if stratified > 0:
        ver_ids = [
            p["path"]
            for p in manifest_programs
            if p.get("path") in claim_pool
            and str(p.get("expected_verdict", "")).upper() == "VERIFIED"
        ]
        ce_ids = [
            p["path"]
            for p in manifest_programs
            if p.get("path") in claim_pool
            and str(p.get("expected_verdict", "")).upper() != "VERIFIED"
        ]
        rng = random.Random(seed)
        rng.shuffle(ver_ids)
        rng.shuffle(ce_ids)
        n_ver = min(len(ver_ids), stratified // 2)
        n_ce = min(len(ce_ids), stratified - n_ver)
        selected = sorted(ver_ids[:n_ver] + ce_ids[:n_ce])
        return selected

    if limit > 0:
        return all_ids[:limit]
    return all_ids


def load_completed_ids(jsonl_path: Path) -> set[str]:
    """Return program IDs already persisted in ``jsonl_path``."""
    completed: set[str] = set()
    if not jsonl_path.exists():
        return completed
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                pid = obj.get("program_id")
                if isinstance(pid, str) and pid:
                    completed.add(pid)
            except json.JSONDecodeError:
                print(
                    f"[warn] Ignoring corrupt JSONL line {line_no} in {jsonl_path}",
                    file=sys.stderr,
                )
    return completed


def _fallback_error_result(
    method_id: str,
    program_id: str,
    source: str,
    claims: list[Claim],
    exc: Exception,
    elapsed: float,
) -> ProgramResult:
    """Never abort a 1,000-program benchmark because of a single program error."""
    claim_results = [
        ClaimResult(
            claim_id=c.claim_id,
            claim_text=c.text,
            status=StatusEnum.UNKNOWN_TIMEOUT,
            anchor=AnchorInfo(
                node_ids=[c.ast_anchor] if c.ast_anchor else [],
                source_lines=[c.source_line] if c.source_line else [],
                has_grounding=method_id in PROPOSED_METHODS,
            ),
            reason=f"benchmark runner caught {type(exc).__name__}: {exc}",
        )
        for c in claims
    ]
    return ProgramResult(
        program_id=program_id,
        source_path=program_id,
        source_text=source,
        n_claims=len(claim_results),
        n_unknown_timeout=len(claim_results),
        claim_results=claim_results,
        elapsed_seconds=elapsed,
        baseline=method_id,
    )


def _run_offline_one(
    args_tuple: tuple[str, str, str, list[Claim], str | None],
) -> ProgramResult:
    """Top-level worker function (picklable for ProcessPoolExecutor on macOS)."""
    method_id, program_id, source, claims, expected_verdict = args_tuple
    started = time.perf_counter()
    try:
        if method_id == "PROPOSED":
            inv_gen = LoopInvariantGenerator(llm_client=None, use_llm=False)
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=program_id,
                baseline=BASELINE_AST_ANCHORED,
                do_replay=True,
                use_llm=False,
                enable_feedback=False,
                enable_invariants=True,
                invariant_generator=inv_gen,
            )
            return res.model_copy(update={"baseline": "PROPOSED"})

        if method_id == "PROPOSED_NO_INV":
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=program_id,
                baseline=BASELINE_AST_ANCHORED,
                do_replay=True,
                use_llm=False,
                enable_feedback=False,
                enable_invariants=False,
                invariant_generator=None,
            )
            return res.model_copy(update={"baseline": "PROPOSED_NO_INV"})

        if method_id == "PROPOSED_NO_REPLAY":
            inv_gen = LoopInvariantGenerator(llm_client=None, use_llm=False)
            res = run_pipeline(
                source,
                program_id=program_id,
                source_path=program_id,
                baseline=BASELINE_AST_ANCHORED,
                do_replay=False,
                use_llm=False,
                enable_feedback=False,
                enable_invariants=True,
                invariant_generator=inv_gen,
            )
            return res.model_copy(update={"baseline": "PROPOSED_NO_REPLAY"})

        # Offline baselines J, K, L
        spec = ProgramSpec(
            program_id=program_id,
            source=source,
            source_path=program_id,
            claims=claims,
            precondition=claims[0].precondition if claims else None,
            metadata={"expected_verdict": expected_verdict} if expected_verdict else {},
        )
        baseline_cls = get_baseline(method_id)
        return baseline_cls().run(spec)
    except Exception as exc:  # noqa: BLE001
        return _fallback_error_result(
            method_id,
            program_id,
            source,
            claims,
            exc,
            time.perf_counter() - started,
        )


def _run_proposed_llm_with_client(
    program_id: str,
    source: str,
    llm_client: LLMClient,
) -> ProgramResult:
    """Run PROPOSED_LLM using any ``LLMClient`` (OpenAI-compatible, Ollama, Stub)."""
    started = time.perf_counter()
    numbered_source = "\n".join(
        f"{index + 1:3d} | {line}"
        for index, line in enumerate(source.splitlines())
    )
    prompt = (
        f"Here is the Python program to verify:\n\n"
        f"```python\n{numbered_source}\n```\n\n"
        "Please analyze the function, provide a concise Chain-of-Thought reasoning trace "
        "(at most 4 sentences) about its state transitions and invariants, and suggest "
        "up to 4 key verification claims tied to the correct line numbers (remembering "
        "that a claim on line L holds in the PRE-STATE immediately before line L executes)."
    )
    system = (
        COT_SYSTEM_PROMPT
        + "\n\nKeep cot_trace concise (<= 4 sentences) and output at most 4 claims.\n"
        "Respond ONLY with a valid compact JSON object matching this schema:\n"
        '{"cot_trace": "<concise reasoning>", "claims": [{"line": <int>, '
        '"expression": "<python boolean expr>"}]}'
    )

    tokens_in = 0
    tokens_out = 0
    cot_trace = ""
    suggested_claims = None
    llm_error_message: str | None = None

    try:
        reply = llm_client.complete(prompt, system=system, temperature=0.1)
        tokens_in = reply.tokens_in
        tokens_out = reply.tokens_out
        plan = LLMVerificationPlan.model_validate(reply.json())
        cot_trace = plan.cot_trace
        suggested_claims = list(plan.claims)
    except Exception as exc:  # noqa: BLE001
        llm_error_message = str(exc)

    inv_gen = LoopInvariantGenerator(llm_client=None, use_llm=False)
    res = run_pipeline(
        source,
        program_id=program_id,
        source_path=program_id,
        baseline=BASELINE_AST_ANCHORED,
        do_replay=True,
        use_llm=False,
        suggested_claims=suggested_claims,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cot_trace=cot_trace,
        llm_error_message=llm_error_message,
        enable_feedback=False,
        enable_invariants=True,
        invariant_generator=inv_gen,
    )
    return res.model_copy(
        update={
            "baseline": "PROPOSED_LLM",
            "elapsed_seconds": time.perf_counter() - started,
        }
    )


def _run_llm_one(
    method_id: str,
    program_id: str,
    source: str,
    claims: list[Claim],
    expected_verdict: str | None,
    *,
    provider: str,
    llm_client: LLMClient | None,
    gemini_api_key: str | None,
    gemini_model: str,
) -> ProgramResult:
    """Run one LLM-backed method (A..I, M, N, or PROPOSED_LLM)."""
    started = time.perf_counter()
    try:
        if method_id == "PROPOSED_LLM":
            if provider == "gemini":
                res = run_pipeline(
                    source,
                    program_id=program_id,
                    source_path=program_id,
                    baseline=BASELINE_AST_ANCHORED,
                    do_replay=True,
                    use_llm=True,
                    gemini_api_key=gemini_api_key,
                    gemini_model=gemini_model,
                    enable_feedback=True,
                    enable_invariants=True,
                )
                return res.model_copy(update={"baseline": "PROPOSED_LLM"})
            assert llm_client is not None
            return _run_proposed_llm_with_client(program_id, source, llm_client)

        spec = ProgramSpec(
            program_id=program_id,
            source=source,
            source_path=program_id,
            claims=claims,
            precondition=claims[0].precondition if claims else None,
            metadata={"expected_verdict": expected_verdict} if expected_verdict else {},
        )
        baseline_cls = get_baseline(method_id)
        return baseline_cls(llm=llm_client).run(spec)
    except Exception as exc:  # noqa: BLE001
        return _fallback_error_result(
            method_id,
            program_id,
            source,
            claims,
            exc,
            time.perf_counter() - started,
        )


def build_llm_client(args: argparse.Namespace) -> tuple[LLMClient, str]:
    """Instantiate the requested LLM client and return ``(client, model_name)``."""
    provider = args.provider.lower()
    max_tokens = args.max_tokens if args.max_tokens > 0 else None
    env_reasoning = (
        args.reasoning_effort
        or os.environ.get("OPENAI_REASONING_EFFORT")
        or os.environ.get("TOKENLAB_REASONING_EFFORT")
        or ""
    )

    if provider == "gemini":
        model = args.model or os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
        client: LLMClient = GeminiClient(api_key=args.api_key, model=model)
    elif provider == "ollama":
        model = args.model or os.environ.get("OLLAMA_MODEL") or "qwen2.5-coder:7b"
        base_url = (
            args.base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://localhost:11434/v1"
        )
        client = OpenAICompatibleClient(
            base_url=base_url,
            model=model,
            api_key=args.api_key,
            timeout=args.timeout,
            seed=args.seed,
            max_tokens=max_tokens,
            reasoning_effort=env_reasoning or None,
        )
    elif provider == "openai":
        model = (
            args.model
            or os.environ.get("OPENAI_MODEL")
            or os.environ.get("TOKENLAB_MODEL")
            or "gpt-4o-mini"
        )
        base_url = (
            args.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("TOKENLAB_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = (
            args.api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("TOKENLAB_API_KEY")
        )
        reasoning_effort = env_reasoning
        if (
            not reasoning_effort
            and "tokenlab" in base_url.lower()
            and "qwen" in model.lower()
        ):
            reasoning_effort = "none"
        client = OpenAICompatibleClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=args.timeout,
            seed=args.seed,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort or None,
        )
    elif provider == "stub":
        model = args.model or "stub"
        client = StubClient(model=model)
    else:
        raise ValueError(f"Unsupported provider: {args.provider!r}")

    if args.rpm > 0:
        client = RateLimitedClient(client, rpm=args.rpm)
    return client, model


def _write_meta(
    meta_path: Path,
    *,
    method: str,
    provider: str,
    model: str,
    dataset: str,
    n_target: int,
    n_completed: int,
    elapsed_wall_s: float,
) -> None:
    payload = {
        "method": method,
        "provider": provider,
        "model": model,
        "dataset": dataset,
        "n_target": n_target,
        "n_completed": n_completed,
        "elapsed_wall_s": round(elapsed_wall_s, 3),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _update_counts(counts: dict[str, int], res: ProgramResult) -> None:
    counts["V"] += res.n_verified
    counts["CE"] += res.n_counterexample
    counts["UNR"] += res.n_unreachable
    counts["UNK"] += res.n_unknown_timeout
    counts["ERR"] += res.n_unsupported + res.n_translation_error


def run_method(
    method_id: str,
    program_ids: list[str],
    sources: dict[str, str],
    claim_pool: dict[str, list[Claim]],
    ground_truth: dict[str, str],
    *,
    out_dir: Path,
    dataset_label: str,
    args: argparse.Namespace,
    llm_client: LLMClient | None,
    llm_model: str,
) -> None:
    """Execute one benchmark method with checkpointing & progress logs."""
    is_offline = method_id in OFFLINE_METHOD_SET
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{method_id}.jsonl"
    meta_path = out_dir / f"{method_id}.meta.json"

    completed_ids = load_completed_ids(jsonl_path) if args.resume else set()
    pending_ids = [pid for pid in program_ids if pid not in completed_ids]

    provider_label = "offline" if is_offline else args.provider.lower()
    model_label = "offline" if is_offline else llm_model

    print(
        f"\n=== [{method_id}] target={len(program_ids)} "
        f"already_done={len(program_ids) - len(pending_ids)} "
        f"pending={len(pending_ids)} provider={provider_label} model={model_label} ===",
        flush=True,
    )

    if not pending_ids:
        _write_meta(
            meta_path,
            method=method_id,
            provider=provider_label,
            model=model_label,
            dataset=dataset_label,
            n_target=len(program_ids),
            n_completed=len(program_ids),
            elapsed_wall_s=0.0,
        )
        print(
            f"    [{method_id}] Nothing to do (all {len(program_ids)} completed).",
            flush=True,
        )
        return

    mode = "a" if args.resume else "w"
    counts = {"V": 0, "CE": 0, "UNR": 0, "UNK": 0, "ERR": 0}
    wall_start = time.perf_counter()
    done_now = 0
    total_pending = len(pending_ids)
    report_every = max(1, args.progress_every)

    def _record(fh, res: ProgramResult) -> None:
        nonlocal done_now
        fh.write(res.model_dump_json() + "\n")
        fh.flush()
        done_now += 1
        _update_counts(counts, res)
        if done_now % report_every == 0 or done_now == total_pending:
            elapsed = time.perf_counter() - wall_start
            rate = done_now / elapsed if elapsed > 0 else 0.0
            eta = (total_pending - done_now) / rate if rate > 0 else 0.0
            print(
                f"    [{method_id}] {done_now}/{total_pending} "
                f"({100.0 * done_now / total_pending:.1f}%) "
                f"elapsed={elapsed:.1f}s eta={eta:.1f}s | "
                f"V={counts['V']} CE={counts['CE']} UNR={counts['UNR']} "
                f"UNK={counts['UNK']} ERR={counts['ERR']}",
                flush=True,
            )

    with jsonl_path.open(mode, encoding="utf-8") as fh:
        if is_offline:
            tasks = [
                (
                    method_id,
                    pid,
                    sources[pid],
                    claim_pool[pid],
                    ground_truth.get(pid),
                )
                for pid in pending_ids
            ]
            workers = max(1, args.workers)
            if workers == 1:
                for task in tasks:
                    _record(fh, _run_offline_one(task))
            else:
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=workers
                ) as pool:
                    for res in pool.map(_run_offline_one, tasks, chunksize=4):
                        _record(fh, res)
        else:
            workers = 1 if args.rpm > 0 else max(1, args.workers)
            if workers == 1:
                for pid in pending_ids:
                    res = _run_llm_one(
                        method_id,
                        pid,
                        sources[pid],
                        claim_pool[pid],
                        ground_truth.get(pid),
                        provider=provider_label,
                        llm_client=llm_client,
                        gemini_api_key=args.api_key,
                        gemini_model=llm_model,
                    )
                    _record(fh, res)
            else:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers
                ) as pool:
                    futures = {
                        pool.submit(
                            _run_llm_one,
                            method_id,
                            pid,
                            sources[pid],
                            claim_pool[pid],
                            ground_truth.get(pid),
                            provider=provider_label,
                            llm_client=llm_client,
                            gemini_api_key=args.api_key,
                            gemini_model=llm_model,
                        ): pid
                        for pid in pending_ids
                    }
                    for fut in concurrent.futures.as_completed(futures):
                        _record(fh, fut.result())

    elapsed_wall = time.perf_counter() - wall_start
    _write_meta(
        meta_path,
        method=method_id,
        provider=provider_label,
        model=model_label,
        dataset=dataset_label,
        n_target=len(program_ids),
        n_completed=len(program_ids) - total_pending + done_now,
        elapsed_wall_s=elapsed_wall,
    )
    print(
        f"=== [{method_id}] Done in {elapsed_wall:.1f}s -> {jsonl_path} ===",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AST-Anchored Neuro-Symbolic Verification benchmarks."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "dataset",
        help="Path to dataset directory containing manifest.json and claim_pool.json.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "results",
        help="Output directory for per-method JSONL checkpoints.",
    )
    parser.add_argument(
        "--methods",
        type=str,
        default="offline",
        help=(
            "Comma-separated methods or aliases: offline (PROPOSED,PROPOSED_NO_INV,J,K,L), "
            "ablation, llm, all, PROPOSED, PROPOSED_NO_INV, PROPOSED_NO_REPLAY, "
            "PROPOSED_LLM, A..N."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run only the first N programs (0 = all).",
    )
    parser.add_argument(
        "--stratified",
        type=int,
        default=0,
        help="Draw a balanced 50/50 stratified subset of N programs (0 = disabled).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for stratified sampling and OpenAI-compatible requests.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(6, os.cpu_count() or 1),
        help="Parallel worker processes (offline) or threads (LLM when --rpm 0).",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume from existing results/<METHOD>.jsonl checkpoints (default: True).",
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai", "ollama", "stub"),
        default="gemini",
        help="LLM backend for LLM-driven baselines.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="",
        help="Base URL for openai/ollama providers (falls back to .env OPENAI_BASE_URL / TOKENLAB_BASE_URL).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Model identifier (falls back to .env OPENAI_MODEL / TOKENLAB_MODEL / provider default).",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Explicit API key override (falls back to .env / environment).",
    )
    parser.add_argument(
        "--rpm",
        type=float,
        default=0.0,
        help="Requests-per-minute throttle for LLM providers (0 = unlimited).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP request timeout in seconds for openai/ollama providers.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Max completion tokens for openai/ollama providers (0 = omit).",
    )
    parser.add_argument(
        "--reasoning-effort",
        type=str,
        default="",
        help=(
            "Optional reasoning_effort for OpenAI-compatible endpoints "
            "(e.g. none, low, medium, high). Auto-defaults to 'none' for Qwen on Token Lab."
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print a progress line every N completed programs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    methods = expand_methods(args.methods)
    dataset_dir = args.dataset.resolve()
    manifest_path = dataset_dir / "manifest.json"
    claim_pool_path = dataset_dir / "claim_pool.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    if not claim_pool_path.exists():
        raise FileNotFoundError(f"Claim pool not found: {claim_pool_path}")

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_programs = manifest_data.get("programs", [])
    claim_pool = load_claim_pool(claim_pool_path)
    ground_truth = load_ground_truth(manifest_path)

    program_ids = select_programs(
        manifest_programs,
        claim_pool,
        limit=args.limit,
        stratified=args.stratified,
        seed=args.seed,
    )
    if not program_ids:
        raise RuntimeError("No matching programs found in dataset.")

    sources: dict[str, str] = {}
    for pid in program_ids:
        src_path = dataset_dir / pid
        sources[pid] = src_path.read_text(encoding="utf-8")

    needs_llm = any(m not in OFFLINE_METHOD_SET for m in methods)
    llm_client: LLMClient | None = None
    llm_model = "offline"
    if needs_llm:
        llm_client, llm_model = build_llm_client(args)

    total_start = time.perf_counter()
    print(
        f"Benchmark starting: dataset={dataset_dir.name} programs={len(program_ids)} "
        f"methods={','.join(methods)} workers={args.workers} resume={args.resume}",
        flush=True,
    )

    for method_id in methods:
        run_method(
            method_id,
            program_ids,
            sources,
            claim_pool,
            ground_truth,
            out_dir=args.out_dir,
            dataset_label=str(args.dataset),
            args=args,
            llm_client=llm_client,
            llm_model=llm_model,
        )

    print(
        f"\nAll methods finished in {time.perf_counter() - total_start:.1f}s. "
        f"Run `python tools/aggregate_results.py --results-dir {args.out_dir}` to tabulate.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

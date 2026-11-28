"""Adapt the upstream Self-Refine loop to the original assertion task."""
from __future__ import annotations

import ast
import contextlib
import io
import json
import subprocess
import time
import traceback
from dataclasses import replace
from pathlib import Path

from ..shared.schemas import ClaimResult, ProgramResult, StatusEnum
from .base import BaseBaseline, ProgramSpec
from .common import program_prompt
from .llm import LLMClient, TokenBudget

METHOD = "SELF_REFINE_OFFICIAL"
UPSTREAM_TASK = "src/commongen/run.py"
UPSTREAM_UTILS = "src/utils.py"
N_ATTEMPTS = 3
VERDICTS = {
    "VERIFIED": StatusEnum.VERIFIED,
    "COUNTEREXAMPLE": StatusEnum.COUNTEREXAMPLE,
    "UNKNOWN": StatusEnum.UNKNOWN_TIMEOUT,
}
VERDICT_SYSTEM = """Decide whether the Python assertion holds for every permitted
input reaching the given line. Check loop entry, exit, and relevant branches.
Treat the target assert as the property to check rather than a path assumption.
Return only a JSON object with verdict (VERIFIED, COUNTEREXAMPLE, or UNKNOWN)
and reason (at most 15 words). Do not put analysis in the reason."""
FEEDBACK_SYSTEM = """Review the candidate verdict against the original Python task.
Give corrections only. Do not issue a verdict or repeat the candidate answer.
Return exactly this JSON shape: {"path_feedback":"none","verdict_feedback":"none"}.
Each value must be a string of at most 12 words. Use "none" if no correction
is needed in that field. Put no analysis or examples in the response."""


def parse_verdict(payload):
    value = payload.get("verdict")
    if not isinstance(value, str):
        raise ValueError("verdict must be a string")
    label = "_".join(value.strip().upper().replace("-", " ").split())
    if label == "COUNTER_EXAMPLE":
        label = "COUNTEREXAMPLE"
    if label == "UNKNOWN_TIMEOUT":
        label = "UNKNOWN"
    if label not in VERDICTS:
        raise ValueError("verdict must be VERIFIED, COUNTEREXAMPLE, or UNKNOWN")
    return {**payload, "verdict": label}


def parse_feedback(payload):
    keys = ("path_feedback", "verdict_feedback")
    if not all(isinstance(payload.get(key), str) for key in keys):
        raise ValueError("path_feedback and verdict_feedback must both be strings")
    feedback = (" ".join(payload[key].split()).replace(",", ";") for key in keys)
    return tuple(value if value and value.lower().rstrip(".") != "none" else "none"
                 for value in feedback)


def load_upstream_function(upstream: Path, commit: str, path: str, name: str, namespace: dict):
    """Load an unchanged upstream function and skip task-specific imports."""
    source = subprocess.check_output(
        ["git", "-C", str(upstream), "show", f"{commit}:{path}"], text=True,
    )
    function, = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    code = compile(ast.Module(body=[function], type_ignores=[]), f"{commit}:{path}", "exec")
    exec(code, namespace)
    return namespace[name]


class OfficialSelfRefine(BaseBaseline):
    id = METHOD
    name = "Self-Refine (upstream loop)"
    requires_llm = True
    supports_precondition = True

    def __init__(self, upstream: Path, out_dir: Path, llm: LLMClient, model: str) -> None:
        self.llm = llm
        commit = subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True,
        ).strip()
        namespace = {"traceback": traceback, "List": list, "ENGINE": model}
        load_upstream_function(upstream, commit, UPSTREAM_UTILS, "retry_parse_fail_prone_cmd", namespace)
        self.loop = load_upstream_function(upstream, commit, UPSTREAM_TASK, "autofb_commongen", namespace)
        self.task_adapters = namespace
        provenance = out_dir / f"{METHOD}.upstream.json"
        identity = {
            "repository": "https://github.com/madaan/self-refine",
            "commit": commit,
            "function": f"{UPSTREAM_TASK}::autofb_commongen",
            "retry_function": f"{UPSTREAM_UTILS}::retry_parse_fail_prone_cmd",
            "model": model,
            "max_attempts": N_ATTEMPTS,
            "temperature": 0.0,
            "response_adapter": "stage_repair_v3",
        }
        if provenance.exists() and json.loads(provenance.read_text()) != identity:
            raise ValueError("Existing Self-Refine run uses a different upstream configuration")
        provenance.write_text(json.dumps(identity, indent=2) + "\n")

    def ask_stage(self, stage, prompt, system, parse, budget, issues):
        request = prompt
        for attempt in (1, 2):
            try:
                reply = self.llm.complete(request, system=system)
            except RuntimeError as exc:
                issues.append({"stage": stage, "attempt": attempt, "error": str(exc)})
                raise
            budget.add(reply)
            try:
                payload = reply.json()
                if not isinstance(payload, dict):
                    raise ValueError("expected a JSON object")
                return parse(payload)
            except ValueError as exc:
                issues.append({
                    "stage": stage, "attempt": attempt, "error": str(exc),
                    "response_preview": reply.text[:300],
                })
                if attempt == 2:
                    raise ValueError(f"{stage}: {exc}") from exc
                request = (
                    f"{prompt}\n\nThe previous reply was rejected: {exc}. "
                    "Try this step again. Return only the JSON object requested by the system."
                )

    def run(self, spec: ProgramSpec) -> ProgramResult:
        started = time.perf_counter()
        budget = TokenBudget()
        results = []
        traces = []
        for claim in spec.claims:
            issues = []
            task = program_prompt(
                replace(spec, claims=[claim], precondition=claim.precondition or spec.precondition),
                task="Decide whether the assertion holds.",
            )

            def generate(stage, prompt):
                answer = self.ask_stage(stage, prompt, VERDICT_SYSTEM, parse_verdict, budget, issues)
                return json.dumps(answer, ensure_ascii=False)

            def initialize(*, concepts):
                return generate("initial_verdict", concepts[0])

            def critique(*, concepts, sentence):
                candidate = json.loads(sentence)
                return self.ask_stage(
                    "feedback",
                    f"{concepts[0]}\n\nCandidate label: {candidate['verdict']}\n"
                    f"Candidate explanation: {candidate.get('reason', '')}\n\n"
                    'Review the candidate. Reply only with {"path_feedback":"...",'
                    '"verdict_feedback":"..."} using short corrections or "none".',
                    FEEDBACK_SYSTEM, parse_feedback, budget, issues,
                )

            def revise(*, concepts, sent_to_fb):
                return generate(
                    "revised_verdict",
                    f"{concepts[0]}\n\nPrevious verdicts and feedback in chronological order:\n"
                    f"{json.dumps(sent_to_fb, ensure_ascii=False)}\n\nReturn the revised verdict."
                )

            self.task_adapters.update(
                CommongenTaskInit=lambda **_: initialize,
                CommongenFeedback=lambda **_: critique,
                CommongenTaskIterate=lambda **_: revise,
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    history = self.loop([task], max_attempts=N_ATTEMPTS)
                if not history:
                    if not issues:
                        raise ValueError("upstream returned no verdict")
                    last = issues[-1]
                    raise ValueError(
                        f"upstream parser retries exhausted at {last['stage']}: {last['error']}"
                    )
                answer = json.loads(history[-1]["sentence"])
                status = VERDICTS[answer["verdict"]]
                reason = str(answer.get("reason") or "")
                trace = {"history": history}
            except (RuntimeError, ValueError) as exc:
                status = StatusEnum.UNKNOWN_TIMEOUT
                reason = f"benchmark runner caught {type(exc).__name__}: {exc}"
                trace = {"error": str(exc)}
            traces.append({"claim_id": claim.claim_id, **trace, "response_issues": issues})
            results.append(ClaimResult(
                claim_id=claim.claim_id, claim_text=claim.text, status=status,
                anchor=self.anchor_info(claim), reason=reason,
            ))
        return self.roll_up(
            spec, self.id, results, started=started, tokens_in=budget.tokens_in,
            tokens_out=budget.tokens_out, cot_trace=json.dumps(traces, ensure_ascii=False),
        )

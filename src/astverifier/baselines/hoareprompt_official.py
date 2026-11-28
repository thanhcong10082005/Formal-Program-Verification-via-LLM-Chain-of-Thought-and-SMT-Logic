"""Adapter for the authors' HoarePrompt CLI and the benchmark LLM client."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

from astverifier.shared.schemas import AnchorInfo, ClaimResult, ProgramResult, StatusEnum

METHOD = "HOAREPROMPT_OFFICIAL"
CONFIG = "config_hoareprompt_qwen32.json"
VERDICTS = {
    "CORRECT": StatusEnum.VERIFIED,
    "INCORRECT": StatusEnum.COUNTEREXAMPLE,
}


def make_result(program_id, source, claim, row):
    verdict = row.get("hoareprompt_verdict")
    status = VERDICTS[verdict] if verdict else StatusEnum.TRANSLATION_ERROR
    return ProgramResult(
        program_id=program_id,
        source_path=program_id,
        source_text=source,
        baseline=METHOD,
        n_claims=1,
        n_verified=int(status == StatusEnum.VERIFIED),
        n_counterexample=int(status == StatusEnum.COUNTEREXAMPLE),
        n_translation_error=int(status == StatusEnum.TRANSLATION_ERROR),
        elapsed_seconds=row["elapsed_s"],
        total_tokens_in=row["tokens_in"],
        total_tokens_out=row["tokens_out"],
        claim_results=[ClaimResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=status,
            anchor=AnchorInfo(node_ids=[], source_lines=[claim.source_line], has_grounding=False),
            reason=row.get("error", f"HoarePrompt predicted {verdict}"),
        )],
    )


class OfficialHoarePrompt:
    """Run serially because the upstream CLI uses process-wide argv and cwd."""

    def __init__(
        self, upstream, inputs, out_dir, client, model, program_ids, sources, claim_pool,
        *, call_limit=30, replay_logs=None,
    ):
        self.upstream = upstream.resolve()
        self.inputs = inputs.resolve()
        self.logs = out_dir.resolve() / "hoareprompt_logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.model = model
        self.call_limit = call_limit
        self.replay_logs = replay_logs
        self.cases = {
            case["program_id"]: case
            for case in json.loads((self.inputs / "cases.json").read_text())
        }
        for pid in program_ids:
            case = self.cases[pid]
            (claim,) = claim_pool[pid]
            if (
                claim.claim_id != case["claim_id"] or claim.source_line != case["source_line"]
                or (self.inputs / case["program"]).read_text() != sources[pid]
            ):
                raise ValueError(f"HoarePrompt input does not match benchmark case {pid}")
        config_path = self.upstream / "configs" / CONFIG
        config = json.loads(config_path.read_text())
        config["model"] = model
        self.config_path = self.logs / "config.json"
        revision = subprocess.check_output(
            ["git", "-C", str(self.upstream), "rev-parse", "HEAD"], text=True
        ).strip()
        provenance = self.logs / "upstream.json"
        identity = {"commit": revision, "config": CONFIG, "model": model}
        if call_limit != 30 or replay_logs is not None:
            identity.update(call_limit=call_limit, replay_logs=str(replay_logs) if replay_logs else None)
        if replay_logs is not None and (
            json.loads((replay_logs / "upstream.json").read_text()).get("commit") != revision
            or json.loads((replay_logs / "config.json").read_text()) != config
        ):
            raise ValueError("HoarePrompt replay requires matching upstream and model configuration")
        for path, data in ((provenance, identity), (self.config_path, config)):
            if path.exists() and json.loads(path.read_text()) != data:
                raise ValueError(f"Existing HoarePrompt run uses a different configuration: {path}")
            path.write_text(json.dumps(data, indent=2) + "\n")

        model_module = types.ModuleType("model")
        model_module.get_model = self.get_model
        old_model = sys.modules.get("model")
        sys.modules["model"] = model_module
        sys.path.insert(0, str(self.upstream / "src"))
        try:
            import hoareprompt
        finally:
            sys.path.pop(0)
            if old_model is None:
                sys.modules.pop("model", None)
            else:
                sys.modules["model"] = old_model
        self.cli = hoareprompt

    def prior_responses(self, program_id):
        if self.replay_logs is None:
            return []
        for path in sorted(
            self.replay_logs.glob(f"{Path(program_id).stem}-*/result.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            row = json.loads(path.read_text())
            if row["program_id"] != program_id or "error" not in row:
                continue
            responses = sorted(
                path.parent.rglob("*.response.md"), key=lambda item: item.stat().st_mtime_ns,
            )
            error = row["error"]
            if "limit of " not in error and "endpoint " not in error:
                # A parser failure may have been caused by the last response.
                responses = responses[:-1]
            return [
                (p.with_suffix("").with_suffix(".prompt.md").read_text(), p.read_text())
                for p in responses
            ]
        return []

    def get_model(self, name, temperature, log_directory=None):
        if name != self.model:
            raise ValueError(f"Unexpected HoarePrompt model: {name}")
        index = 0

        def query(prompt):
            nonlocal index
            if self.row["calls"] >= self.call_limit:
                raise RuntimeError(
                    f"Reached the benchmark limit of {self.call_limit} model calls per sample"
                )
            replay_index = self.row["calls"]
            self.row["calls"] += 1
            if log_directory:
                log_directory.mkdir(parents=True, exist_ok=True)
                (log_directory / f"{index:04}.prompt.md").write_text(prompt)
            if replay_index < len(self.replay) and self.replay[replay_index][0] == prompt:
                response = self.replay[replay_index][1]
                self.row["reused_calls"] += 1
            else:
                self.replay = []
                reply = self.client.complete(prompt, temperature=temperature)
                response = reply.text
                self.row["tokens_in"] += reply.tokens_in
                self.row["tokens_out"] += reply.tokens_out
            if log_directory:
                (log_directory / f"{index:04}.response.md").write_text(response)
                index += 1
            return response

        return types.SimpleNamespace(query=query)

    def run(self, program_id, source, claims):
        case = self.cases[program_id]
        attempt = Path(tempfile.mkdtemp(prefix=Path(program_id).stem + "-", dir=self.logs))
        console = attempt / "console.log"
        self.row = {
            "program_id": program_id, "calls": 0, "reused_calls": 0,
            "tokens_in": 0, "tokens_out": 0,
        }
        self.replay = self.prior_responses(program_id)
        started = time.monotonic()
        previous_argv = sys.argv
        previous_cwd = Path.cwd()
        src_dir = str(self.upstream / "src")
        sys.path.insert(0, src_dir)
        try:
            os.chdir(self.upstream)
            sys.argv = [
                "hoareprompt.py", "--command", "assess", "--config", str(self.config_path),
                "--program", str(self.inputs / case["program"]),
                "--description", str(self.inputs / case["description"]),
                "--log", str(attempt / "trace"),
            ]
            with console.open("w") as output, contextlib.redirect_stdout(output):
                self.cli.main()
            (verdict,) = [line.strip() for line in console.read_text().splitlines()
                          if line.strip() in VERDICTS]
            self.row["hoareprompt_verdict"] = verdict
        except (Exception, SystemExit) as exc:
            self.row["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            sys.argv = previous_argv
            sys.path.remove(src_dir)
            os.chdir(previous_cwd)
        self.row["elapsed_s"] = round(time.monotonic() - started, 3)
        (attempt / "result.json").write_text(json.dumps(self.row, indent=2) + "\n")
        return make_result(program_id, source, claims[0], self.row)

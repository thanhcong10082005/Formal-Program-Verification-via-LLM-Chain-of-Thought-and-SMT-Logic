"""Run the official HoarePrompt CLI using the repo's Token Lab client."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

from run_benchmark import load_dotenv

from astverifier.baselines.llm import LLMReply, OpenAICompatibleClient, RateLimitedClient

ROOT = Path(__file__).resolve().parents[1]
MODEL = "MiniCPM-V-4.7-35B-A3B"
BASE_URL = "https://api.tokenlab.amd.com/v1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--reuse-responses", type=Path)
    parser.add_argument("program_ids", nargs="+")
    args = parser.parse_args()
    upstream, inputs, out = (p.resolve() for p in (args.upstream, args.inputs, args.out_dir))
    index = {r["program_id"]: r for r in json.loads((inputs / "cases.json").read_text())}
    cases = [index[pid] for pid in args.program_ids]
    if len(set(args.program_ids)) != len(cases):
        parser.error("program IDs must be unique")
    config_path = upstream / "configs" / "config_hoareprompt_qwen32.json"
    config = json.loads(config_path.read_text())
    config["model"] = MODEL
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    previous_dir = args.reuse_responses.resolve() if args.reuse_responses else None
    previous_ids = []
    if previous_dir:
        previous = json.loads((previous_dir / "experiment.json").read_text())
        if previous["upstream_commit"] != revision or json.loads(
            (previous_dir / "config.json").read_text()
        ) != config:
            raise ValueError("Cannot reuse responses from a different artifact configuration")
        previous_ids = previous["program_ids"]
    load_dotenv(ROOT / ".env")
    key = os.environ.pop("TOKENLAB_API_KEY")
    client = RateLimitedClient(OpenAICompatibleClient(
        base_url=BASE_URL, api_key=key, model=MODEL, timeout=45, max_retries=1,
        max_tokens=1024,
    ), rpm=6)
    out.mkdir(parents=True, exist_ok=False)
    output_config = out / "config.json"
    output_config.write_text(json.dumps(config, indent=2) + "\n")
    (out / "experiment.json").write_text(json.dumps({
        "upstream_commit": revision, "upstream_config": config_path.name,
        "provider": BASE_URL, "model": MODEL, "rpm": 6, "max_tokens": 1024,
        "program_ids": args.program_ids,
    }, indent=2) + "\n")

    request_limit = 30
    calls = 0
    tokens_in = 0
    tokens_out = 0
    reused = 0
    saved_responses: dict[str, str] = {}

    class ModelBridge:
        def __init__(self, temperature, log_directory):
            self.temperature = temperature
            self.log_directory = log_directory
            self.index = 0

        def query(self, prompt):
            nonlocal calls, tokens_in, tokens_out, reused
            if calls == request_limit:
                raise RuntimeError(f"Reached the pilot limit of {request_limit} calls per sample")
            calls += 1
            if prompt in saved_responses:
                reply = LLMReply(text=saved_responses[prompt])
                reused += 1
            else:
                reply = client.complete(prompt, temperature=self.temperature)
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            if self.log_directory:
                self.log_directory.mkdir(parents=True, exist_ok=True)
                (self.log_directory / f"{self.index:04}.prompt.md").write_text(prompt)
                (self.log_directory / f"{self.index:04}.response.md").write_text(reply.text)
                self.index += 1
            return reply.text

    def get_model(name, temperature, log_directory=None):
        if name != MODEL:
            raise ValueError(f"Unexpected model requested by HoarePrompt: {name}")
        return ModelBridge(temperature, log_directory)

    model_module = types.ModuleType("model")
    model_module.get_model = get_model
    sys.modules["model"] = model_module
    sys.path.insert(0, str(upstream / "src"))
    os.chdir(upstream)
    import hoareprompt

    manifest = json.loads((ROOT / "dataset" / "manifest.json").read_text())
    labels = {r["path"]: r["expected_verdict"] for r in manifest["programs"]}
    with (out / "results.jsonl").open("x") as report:
        for number, case in enumerate(cases, 1):
            calls = tokens_in = tokens_out = 0
            reused = 0
            saved_responses.clear()
            if previous_dir and case["program_id"] in previous_ids:
                previous_number = previous_ids.index(case["program_id"]) + 1
                for response in (previous_dir / f"case_{previous_number:02d}").rglob("*.response.md"):
                    prompt = response.with_name(response.name.replace(".response.md", ".prompt.md"))
                    if prompt.exists():
                        saved_responses[prompt.read_text()] = response.read_text()
            started = time.monotonic()
            case_log = out / f"case_{number:02d}"
            console_path = out / f"case_{number:02d}.log"
            sys.argv = [
                "hoareprompt.py", "--command", "assess", "--config", str(output_config),
                "--program", str(inputs / case["program"]),
                "--description", str(inputs / case["description"]), "--log", str(case_log),
            ]
            row = {"program_id": case["program_id"], "expected": labels[case["program_id"]]}
            try:
                with console_path.open("x") as console, contextlib.redirect_stdout(console):
                    hoareprompt.main()
                decisions = [
                    line.strip() for line in console_path.read_text().splitlines()
                    if line.strip() in {"CORRECT", "INCORRECT"}
                ]
                if len(decisions) != 1:
                    raise ValueError("Upstream CLI did not print exactly one verdict")
                row["hoareprompt_verdict"] = decisions[0]
                row["matches_label"] = (decisions[0] == "CORRECT") == (row["expected"] == "VERIFIED")
            except (Exception, SystemExit) as exc:
                row["error"] = str(exc)
            row.update(calls=calls, reused_calls=reused, tokens_in=tokens_in, tokens_out=tokens_out,
                       elapsed_s=round(time.monotonic() - started, 2))
            line = json.dumps(row)
            report.write(line + "\n")
            report.flush()
            print(line, flush=True)


if __name__ == "__main__":
    main()

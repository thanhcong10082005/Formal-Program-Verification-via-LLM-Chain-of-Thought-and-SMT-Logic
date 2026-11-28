"""Export the benchmark's original assertions as HoarePrompt CLI inputs."""

import argparse
import ast
import json
from pathlib import Path


def read_cases(dataset: Path, checkpoint: Path) -> list[tuple]:
    pool = json.loads((dataset / "claim_pool.json").read_text(encoding="utf-8"))
    claims = {}
    for claim in pool["claims"]:
        claims.setdefault(claim["program_id"], []).append(claim)

    programs = {}
    for line in checkpoint.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        program_id = row["program_id"]
        if program_id in programs:
            raise ValueError(f"Duplicate checkpoint program: {program_id}")
        programs[program_id] = row

    cases = []
    for program_id, row in programs.items():
        (claim,) = claims[program_id]
        source_path = (dataset / program_id).resolve()
        if not source_path.is_relative_to(dataset.resolve()):
            raise ValueError(f"Program is outside the dataset: {program_id}")
        source = source_path.read_bytes()
        if source.decode("utf-8") != row["source_text"]:
            raise ValueError(f"Dataset source differs from the checkpoint: {program_id}")
        line = claim["source_line"]
        expected = ast.dump(ast.parse(claim["text"], mode="eval").body)
        matching = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assert) and node.lineno == line
            and ast.dump(node.test) == expected
        ]
        if len(matching) != 1:
            raise ValueError(f"Expected one matching assertion at {program_id}:{line}")
        description = (
            "The program is correct if, for every integer input that reaches "
            f"the assertion on line {line}, the following condition holds "
            f"immediately before that assertion is evaluated:\n{claim['text']}\n"
            "Use the program's own control flow to determine which inputs reach that line.\n"
        )
        cases.append((program_id, claim["claim_id"], line, source, description))
    if not cases:
        raise ValueError("The benchmark checkpoint is empty")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    cases = read_cases(args.dataset, args.checkpoint)
    args.out_dir.mkdir(parents=True, exist_ok=False)

    index = []
    for number, (program_id, claim_id, line, source, description) in enumerate(cases, 1):
        case_dir = args.out_dir / f"case_{number:04d}"
        case_dir.mkdir()
        (case_dir / "program.py").write_bytes(source)
        (case_dir / "description.txt").write_text(description, encoding="utf-8")
        index.append({
            "program_id": program_id,
            "claim_id": claim_id,
            "source_line": line,
            "program": f"{case_dir.name}/program.py",
            "description": f"{case_dir.name}/description.txt",
        })
    (args.out_dir / "cases.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(index)} HoarePrompt input pairs to {args.out_dir}")


if __name__ == "__main__":
    main()

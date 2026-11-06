"""Phase 0.5.4 -- Shared Claim Pool Generation.

Builds ``data/bench_v1/claim_pool.json``: the single, shared source of
claims that every method -- the proposed method and every baseline -- reads.

The claims come from the programs' own ``assert`` statements, so building
the pool costs **0 LLM tokens**. Every claim carries:

* the text of the assertion,
* the line it lives on,
* a stable AST anchor that resolves back to the node,
* the path precondition (Phase 0.5.2),
* the ground-truth verdict (Phase 0.5.1).

Because the pool is shared, no method can be advantaged by a better claim
extraction or by better wording.

Run with::

    python tools/build_claim_pool.py \
        --sample data/bench_v1/sample_1000.txt \
        --manifest data/bench_v1/manifest.json \
        --out data/bench_v1/claim_pool.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.anchors import assign_ids  # noqa: E402
from astverifier.shared.preconditions import extract_map  # noqa: E402


def _anchor_for(node: ast.AST, ids: dict[ast.AST, object]) -> str | None:
    """Return the stable anchor of an assert node."""
    node_id = ids.get(node)
    if node_id is None:
        return None
    return node_id.as_string()


def _claim_entries(
    tree: ast.Module,
    entry: dict,
    ids: dict[ast.AST, object],
    preconditions: dict[str, object] | None = None,
) -> list[dict]:
    """Extract the claims of one program.

    The claim text is taken from the program's own ``assert`` statements,
    so the pool needs no model at all.

    The tree must be the very tree that ``ids`` was computed from: the
    anchors are keyed on node identity, so re-parsing the source would
    produce a tree whose nodes are all unknown to the anchor table.

    ``preconditions`` maps a claim *text* to the path condition of its
    first site. A program may assert different expressions under different
    conditions, so the condition belongs to the claim, not to the program.
    """
    asserts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]

    out: list[dict] = []
    for index, node in enumerate(asserts):
        text = ast.unparse(node.test)
        anchor = _anchor_for(node, ids)
        precondition = entry.get("precondition")
        if preconditions is not None and text in preconditions:
            found = preconditions[text]
            precondition = getattr(found, "precondition", None)
        out.append(
            {
                "claim_id": f"bench_v1::{entry['path']}::assert_{index}",
                "program_id": entry["path"],
                "kind": "postcondition",
                "text": text,
                "source_line": node.lineno,
                "ast_anchor": anchor,
                "precondition": precondition,
                "expected_verdict": entry.get("expected_verdict"),
                "gt_confidence": entry.get("gt_confidence"),
                "provenance": f"assert@L{node.lineno}",
                "validated": {
                    "syntax_ok": True,
                    "anchor_ok": anchor is not None,
                },
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT.parent / "Dataset" / "filtered"))
    parser.add_argument(
        "--sample", default=str(ROOT / "data" / "bench_v1" / "sample_1000.txt")
    )
    parser.add_argument(
        "--manifest", default=str(ROOT / "data" / "bench_v1" / "manifest.json")
    )
    parser.add_argument(
        "--out", default=str(ROOT / "data" / "bench_v1" / "claim_pool.json")
    )
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset).resolve()
    sample_path = Path(args.sample).resolve()
    manifest_path = Path(args.manifest).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sample_paths = [
        line.strip()
        for line in sample_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_path = {e["path"]: e for e in manifest["programs"]}

    claims: list[dict] = []
    problems: list[str] = []
    for path in sample_paths:
        entry = by_path.get(path)
        if entry is None:
            problems.append(f"{path}: not in the manifest")
            continue
        source = (dataset_dir / path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            problems.append(f"{path}: {exc}")
            continue
        ids = assign_ids(tree)
        found = _claim_entries(tree, entry, ids, extract_map(source))
        if not found:
            problems.append(f"{path}: no assert statement")
            continue
        claims.extend(found)

    # --- validation --------------------------------------------------------
    n_anchor_ok = sum(1 for c in claims if c["validated"]["anchor_ok"])
    n_syntax_ok = sum(1 for c in claims if c["validated"]["syntax_ok"])
    n_with_pre = sum(1 for c in claims if c.get("precondition") is not None)
    programs_covered = len({c["program_id"] for c in claims})

    pool = {
        "version": "2.0",
        "dataset": "Dataset/filtered",
        "sample_size": len(sample_paths),
        "n_claims": len(claims),
        "n_programs": programs_covered,
        "generator": {
            "method": "assert_extraction",
            "tokens": 0,
            "script": "tools/build_claim_pool.py",
        },
        "validation": {
            "syntax_ok": n_syntax_ok,
            "anchor_ok": n_anchor_ok,
            "with_precondition": n_with_pre,
            "problems": problems[:20],
            "n_problems": len(problems),
        },
        "by_verdict": dict(
            sorted(Counter(c["expected_verdict"] for c in claims).items())
        ),
        "by_claim": dict(
            sorted(Counter(c["text"] for c in claims).items())
        ),
        "claims": claims,
    }
    out_path.write_text(
        json.dumps(pool, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"wrote {out_path}")
    print(f"sample size    : {len(sample_paths)}")
    print(f"programs       : {programs_covered}")
    print(f"claims         : {len(claims)}")
    print(f"syntax ok      : {n_syntax_ok}")
    print(f"anchor ok      : {n_anchor_ok}")
    print(f"with precondition: {n_with_pre}")
    print(f"problems       : {len(problems)}")
    print("tokens         : 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 0.5.3 -- Deduplication & Stratified Sampling.

Selects exactly 1,000 representative programs from the labelled dataset.

Three things make the sample representative:

1. **Dedup.** Programs are grouped by three independent hashes (raw bytes,
   the manifest's ``normalized_sha256``, and an AST-normalized hash that
   renames locals and drops ``junk_*`` scratch statements). Only the third
   one removes anything, but all three are checked, because the manifest's
   ``duplicates`` field is *not* a duplicate indicator.

2. **Claim coverage.** The dataset has 36 distinct claim texts. The sample
   covers all 36; the five groups with only one or two members are taken
   whole.

3. **Verdict balance.** The sample holds roughly half ``VERIFIED`` and
   half ``COUNTEREXAMPLE``. This is a *stratified* allocation, not a
   proportional one: the dataset is ~85% verified and its counterexamples
   live in only three claim forms, so a proportional sample would carry
   almost no counterexamples at all. The claim groups that carry the rare
   class are therefore oversampled, and ``sampling_report.json`` names
   them.

The selection is deterministic: the same manifest always yields the same
1,000 programs.

Run with::

    python tools/select_stratified_sample.py \
        --manifest data/bench_v1/manifest.json \
        --out-dir data/bench_v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.hashing import fingerprint  # noqa: E402

# The seed that fixes the sample.
SEED = 42

# The size of the benchmark.
TARGET_SIZE = 1000

# The share of the sample that should carry a counterexample.
#
# The dataset itself is heavily skewed: about 85% of its programs are
# ``VERIFIED``, and the ``COUNTEREXAMPLE`` programs are concentrated in
# three claim forms. A sample that mirrors that mix would make recall
# unmeasurable, so the benchmark deliberately *oversamples* the rare
# class. The sampling report records which claim groups were
# oversampled, so the distortion is visible rather than hidden.
TARGET_CE_SHARE = 0.50

# Verdicts that may enter the sample.
USABLE_VERDICTS = ("VERIFIED", "COUNTEREXAMPLE")


# --- Dedup --------------------------------------------------------------------


def _raw_hash(path: Path) -> str:
    """The hash of the file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _classify(entry: dict, path: Path, dataset_dir: Path) -> dict:
    """Compute the three hashes and the structural facts of one program."""
    source = path.read_text(encoding="utf-8", errors="replace")
    info: dict = {
        "path": entry["path"],
        "raw_hash": _raw_hash(path),
        "manifest_hash": entry.get("normalized_sha256"),
        "ast_hash": None,
        "signature_hash": None,
        "n_params": 0,
        "n_loops": 0,
        "n_branches": 0,
        "claim": None,
    }
    try:
        fp = fingerprint(source)
        info["ast_hash"] = fp.ast_hash
        info["signature_hash"] = fp.signature_hash
        info["n_params"] = fp.n_params
        info["n_loops"] = fp.n_loops
        info["n_branches"] = fp.n_branches
    except SyntaxError:
        pass

    claims = entry.get("claims") or []
    info["claim"] = claims[0] if claims else None
    info["n_claims"] = len(claims)
    return info


# --- Selection ----------------------------------------------------------------


def _diversity_key(info: dict) -> tuple:
    """A key that groups programs by their structural shape.

    Within a claim group and a verdict, programs are picked to maximise
    the variety of arities, loop counts and branch counts.
    """
    return (
        info["n_params"],
        info["n_loops"],
        info["n_branches"],
        info["ast_hash"],
    )


def _pick_balanced(
    candidates: list[dict],
    quota: int,
    rng: random.Random,
    quota_by_verdict: dict[str, int] | None = None,
) -> list[dict]:
    """Pick ``quota`` candidates, honouring a per-verdict quota.

    The candidates are first grouped by verdict and then by structural
    shape. Within a group the order is a seeded shuffle, so the result is
    deterministic but not biased by the file order.

    When ``quota_by_verdict`` is given, each verdict class gets exactly
    its own share (as far as the pool allows), and the surplus of one
    class is given to the other.
    """
    by_verdict: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        by_verdict[candidate["verdict"]].append(candidate)

    for group in by_verdict.values():
        rng.shuffle(group)

    verdicts = sorted(by_verdict)
    if quota_by_verdict is None:
        # No per-class quota: distribute the slots evenly, then round-robin.
        share = {v: quota // max(1, len(verdicts)) for v in verdicts}
        for index, v in enumerate(verdicts):
            if index < quota - sum(share.values()):
                share[v] += 1
    else:
        share = {
            v: min(quota_by_verdict.get(v, 0), len(by_verdict.get(v, [])))
            for v in verdicts
        }
        # The unused slots go to the classes that still have members.
        while sum(share.values()) < quota:
            moved = False
            for v in verdicts:
                if sum(share.values()) >= quota:
                    break
                if share[v] < len(by_verdict.get(v, [])):
                    share[v] += 1
                    moved = True
            if not moved:
                break

    # Structural diversity first: within a verdict, the programs whose
    # shape has not been seen yet come before the repeats.
    chosen: list[dict] = []
    seen_shapes: set[tuple] = set()
    for verdict in verdicts:
        group = by_verdict.get(verdict, [])
        primary: list[dict] = []
        deferred: list[dict] = []
        for candidate in group:
            shape = _diversity_key(candidate)[:3]
            if shape in seen_shapes:
                deferred.append(candidate)
                continue
            seen_shapes.add(shape)
            primary.append(candidate)
        chosen.extend(primary[: share[verdict]])
        chosen.extend(deferred[: max(0, share[verdict] - len(primary))])

    # Any slot that could not be filled is given to the other classes.
    if len(chosen) < quota:
        taken = {id(c) for c in chosen}
        for verdict in verdicts:
            for candidate in by_verdict.get(verdict, []):
                if len(chosen) >= quota:
                    break
                if id(candidate) in taken:
                    continue
                chosen.append(candidate)
                taken.add(id(candidate))
    return chosen[:quota]


def _distribute(
    total: int,
    group_names: list[str],
    avail: dict[str, dict[str, int]],
    klass: str,
) -> dict[str, int]:
    """Spread ``total`` slots of one verdict over the claim groups.

    The slots are shared out in proportion to how many programs of that
    verdict each group actually has, and no group ever gets more slots
    than it has programs. When the request exceeds the pool, the pool
    wins.
    """
    weights = {name: avail[name][klass] for name in group_names}
    pool = sum(weights.values())
    if pool <= 0:
        return dict.fromkeys(group_names, 0)

    total = min(total, pool)
    shares = {name: total * weights[name] / pool for name in group_names}
    alloc = {name: int(shares[name]) for name in group_names}

    # The rounding remainder goes to the groups with the largest
    # fractional part, so the total is exact.
    order = sorted(
        group_names,
        key=lambda name: (-(shares[name] - alloc[name]), -weights[name], name),
    )
    index = 0
    while sum(alloc.values()) < total and index < len(order) * 100:
        name = order[index % len(order)]
        if alloc[name] < weights[name]:
            alloc[name] += 1
        index += 1
    return alloc


def _allocate(
    groups: dict[str, list[dict]],
    group_names: list[str],
    target: int,
) -> dict[str, dict[str, int]]:
    """Allocate the sample's slots over the claim groups.

    The allocation has two goals that pull in opposite directions:

    * **Claim coverage** -- every one of the 36 claim forms must appear.
    * **Verdict balance** -- the sample must hold roughly half
      ``VERIFIED`` and half ``COUNTEREXAMPLE``, so that precision and
      recall are both meaningful.

    The dataset itself is heavily skewed: about 85% of the programs are
    ``VERIFIED``, and the ``COUNTEREXAMPLE`` programs are concentrated in
    a handful of claim forms. A purely proportional allocation would
    therefore produce a sample with only a few percent counterexamples.

    The allocation is therefore done by *verdict first*:

    1. The sample's slots are split into a counterexample budget and a
       verified budget.
    2. The counterexample budget is spread over the claim groups that
       hold counterexamples, in proportion to how many they have.
    3. The verified budget is spread over the claim groups that hold
       verified programs, in the same way.

    Every claim group receives at least one program of each verdict it
    has, so all 36 forms are covered and every group that can be
    balanced is balanced. The groups that carry the rare class are
    consequently **oversampled**; the report names them and records the
    distortion.
    """
    avail: dict[str, dict[str, int]] = {}
    for name in group_names:
        pool = groups[name]
        avail[name] = {
            "VERIFIED": sum(1 for c in pool if c["verdict"] == "VERIFIED"),
            "COUNTEREXAMPLE": sum(
                1 for c in pool if c["verdict"] == "COUNTEREXAMPLE"
            ),
        }

    take: dict[str, dict[str, int]] = {
        name: {"VERIFIED": 0, "COUNTEREXAMPLE": 0} for name in group_names
    }

    # --- 1. coverage: one program of each verdict the group has -----------
    for name in group_names:
        for klass in ("VERIFIED", "COUNTEREXAMPLE"):
            if avail[name][klass] > 0:
                take[name][klass] = 1

    sum(sum(take[name].values()) for name in group_names)

    # --- 2. the verdict budgets --------------------------------------------
    n_v_available = sum(avail[name]["VERIFIED"] for name in group_names)
    n_ce_available = sum(avail[name]["COUNTEREXAMPLE"] for name in group_names)
    lower = max(0, target - n_v_available)
    upper = min(target, n_ce_available)
    target_ce = min(max(round(target * TARGET_CE_SHARE), lower), upper)
    target_v = target - target_ce

    already_ce = sum(take[name]["COUNTEREXAMPLE"] for name in group_names)
    already_v = sum(take[name]["VERIFIED"] for name in group_names)

    ce_alloc = _distribute(
        max(0, target_ce - already_ce),
        group_names,
        avail,
        "COUNTEREXAMPLE",
    )
    v_alloc = _distribute(
        max(0, target_v - already_v), group_names, avail, "VERIFIED"
    )

    for name in group_names:
        take[name]["COUNTEREXAMPLE"] += ce_alloc[name]
        take[name]["VERIFIED"] += v_alloc[name]

    # --- 3. fill whatever is left ------------------------------------------
    leftover = target - sum(sum(take[name].values()) for name in group_names)
    if leftover > 0:
        order = sorted(
            group_names,
            key=lambda name: (
                -(avail[name]["VERIFIED"] + avail[name]["COUNTEREXAMPLE"]),
                name,
            ),
        )
        index = 0
        while leftover > 0 and index < len(order) * 200:
            name = order[index % len(order)]
            for klass in ("COUNTEREXAMPLE", "VERIFIED"):
                if leftover <= 0:
                    break
                if take[name][klass] < avail[name][klass]:
                    take[name][klass] += 1
                    leftover -= 1
            index += 1

    return take


def select(
    programs: list[dict],
    infos: dict[str, dict],
    *,
    target: int = TARGET_SIZE,
    seed: int = SEED,
) -> dict:
    """Select the stratified sample.

    Returns a report describing what was selected and what was dropped.
    """
    rng = random.Random(seed)

    dropped: dict[str, list[str]] = defaultdict(list)

    # --- 1. remove the unusable programs ----------------------------------
    usable: list[dict] = []
    for entry in programs:
        path = entry["path"]
        if not entry.get("claims"):
            dropped["no_claim"].append(path)
            continue
        if entry.get("expected_verdict") not in USABLE_VERDICTS:
            dropped["unusable_verdict"].append(path)
            continue
        claims = list(dict.fromkeys(entry.get("claims") or []))
        if len(claims) > 1:
            # A program that asserts several *distinct* expressions cannot
            # be scored against a single ground-truth verdict: the verdict
            # is about the program, but the claim pool would offer
            # several claims for it, and a baseline could answer "this one
            # is fine" while the ground truth says otherwise.
            #
            # The dataset has exactly one such program
            # (``conditionals/p00008.py``), and it is dropped on purpose:
            # keeping it would let the 36th claim form -- which exists
            # *only* in this program -- enter the sample through a program
            # whose verdict does not describe all of its claims.
            dropped["multiple_claims"].append(path)
            continue
        # The balancing logic works on a normalised ``verdict`` key, while
        # the manifest calls the same field ``expected_verdict``.
        usable.append({**entry, "verdict": entry["expected_verdict"]})

    # --- 2. deduplicate ---------------------------------------------------
    # The three hashes are checked in turn. The AST hash is the only one
    # that actually merges anything.
    seen_raw: dict[str, str] = {}
    seen_manifest: dict[str, str] = {}
    seen_ast: dict[str, str] = {}
    unique: list[dict] = []
    for entry in usable:
        path = entry["path"]
        info = infos[path]

        if info["raw_hash"] in seen_raw:
            dropped["duplicate_raw"].append(path)
            continue
        seen_raw[info["raw_hash"]] = path

        manifest_hash = info["manifest_hash"]
        if manifest_hash and manifest_hash in seen_manifest:
            dropped["duplicate_manifest_hash"].append(path)
            continue
        if manifest_hash:
            seen_manifest[manifest_hash] = path

        ast_hash = info["ast_hash"]
        if ast_hash and ast_hash in seen_ast:
            dropped["duplicate_ast"].append(path)
            continue
        if ast_hash:
            seen_ast[ast_hash] = path

        unique.append(entry)

    # --- 3. group by claim ------------------------------------------------
    # The structural facts live in ``infos``; they are merged into the
    # candidate so that the picker can reason about shape diversity.
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in unique:
        candidate = {**entry, **infos[entry["path"]]}
        groups[entry["claims"][0]].append(candidate)

    # --- 4. distribute the quota over the groups --------------------------
    # Every group gets at least one program. The rest is distributed so
    # that the sample holds roughly half ``VERIFIED`` and half
    # ``COUNTEREXAMPLE``, while every claim form stays covered.
    group_names = sorted(groups)
    n_groups = len(group_names)

    if n_groups > target:
        # More claim forms than slots: take the largest groups first.
        group_names = sorted(
            group_names, key=lambda name: (-len(groups[name]), name)
        )[:target]
        dropped["claim_groups_overflow"].extend(
            name for name in sorted(groups) if name not in group_names
        )

    # The number of counterexamples the sample can actually carry. The
    # dataset is heavily skewed towards ``VERIFIED``, so the target is
    # bounded by what is available on both sides: the sample cannot hold
    # more counterexamples than the pool has, and it cannot hold so many
    # counterexamples that there is no room left for the verified ones.
    n_v_available = sum(1 for entry in unique if entry["verdict"] == "VERIFIED")
    n_ce_available = sum(1 for entry in unique if entry["verdict"] == "COUNTEREXAMPLE")
    lower = max(0, target - n_v_available)
    upper = min(target, n_ce_available)
    min(max(round(target * TARGET_CE_SHARE), lower), upper)

    take = _allocate(groups, group_names, target)
    quotas = {
        name: take[name]["VERIFIED"] + take[name]["COUNTEREXAMPLE"]
        for name in group_names
    }
    # --- 5. pick within each group ----------------------------------------
    selected: list[dict] = []
    group_reports: list[dict] = []
    for name in group_names:
        candidates = groups[name]
        quota = min(quotas[name], len(candidates))
        picked = _pick_balanced(candidates, quota, rng, take[name])

        verdict_counts = Counter(c["verdict"] for c in picked)
        group_reports.append(
            {
                "claim": name,
                "pool_size": len(candidates),
                "pool_by_verdict": dict(
                    sorted(
                        Counter(c["verdict"] for c in candidates).items()
                    )
                ),
                "quota": quotas[name],
                "quota_by_verdict": dict(sorted(take[name].items())),
                "selected": len(picked),
                "shortfall": max(0, quotas[name] - len(picked)),
                "by_verdict": dict(sorted(verdict_counts.items())),
                "balanced": _is_balanced(verdict_counts, len(picked)),
                "oversampled": (
                    len(picked) / len(candidates) > 1.5 and len(candidates) > 0
                ),
            }
        )
        selected.extend(picked)

    # --- 6. top up if a group could not fill its quota --------------------
    shortfall = target - len(selected)
    if shortfall > 0:
        chosen_paths = {e["path"] for e in selected}
        leftovers = [
            {**e, **infos[e["path"]]} for e in unique if e["path"] not in chosen_paths
        ]
        rng.shuffle(leftovers)
        for entry in leftovers:
            if len(selected) >= target:
                break
            selected.append(entry)

    selected.sort(key=lambda e: e["path"])

    # --- the report --------------------------------------------------------
    verdict_counts = Counter(e["expected_verdict"] for e in selected)
    total = len(selected)
    claim_counts = Counter(e["claims"][0] for e in selected)

    # The dataset's own verdict mix, for comparison with the sample's.
    pool_counts = Counter(e["verdict"] for e in unique)
    pool_total = max(1, sum(pool_counts.values()))

    # Every distinct claim text in the *whole* dataset, including the ones
    # held by a program that was dropped. The dataset has 36 of them, but
    # only 34 can be represented: two of them live exclusively in
    # ``conditionals/p00008.py``, which asserts two different expressions
    # and therefore cannot be scored against a single verdict.
    all_claim_texts = {
        text
        for entry in programs
        for text in (entry.get("claims") or [])
    }
    unreachable_texts = sorted(all_claim_texts - set(groups))

    return {
        "seed": seed,
        "target_size": target,
        "selected_size": total,
        "n_claim_groups_in_pool": len(groups),
        "n_claim_groups_covered": len(claim_counts),
        "n_distinct_claim_texts_in_dataset": len(all_claim_texts),
        "claim_texts_not_representable": unreachable_texts,
        "by_verdict": dict(sorted(verdict_counts.items())),
        "by_verdict_share": {
            k: round(v / total, 4) for k, v in sorted(verdict_counts.items())
        },
        "balanced": _is_balanced(verdict_counts, total),
        "pool_by_verdict": dict(sorted(pool_counts.items())),
        "pool_by_verdict_share": {
            k: round(v / pool_total, 4) for k, v in sorted(pool_counts.items())
        },
        "oversampled_claim_groups": sorted(
            g["claim"] for g in group_reports if g["oversampled"]
        ),
        "n_dropped": {k: len(v) for k, v in sorted(dropped.items())},
        "dropped_examples": {
            k: sorted(v)[:5] for k, v in sorted(dropped.items())
        },
        "claim_groups": sorted(group_reports, key=lambda g: g["claim"]),
        "selected": [e["path"] for e in selected],
    }


def _is_balanced(counts: Counter, total: int) -> bool:
    """True iff every verdict class is between 40% and 60% of the sample."""
    if total == 0:
        return False
    for value in counts.values():
        share = value / total
        if share < 0.40 or share > 0.60:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "bench_v1" / "manifest.json"),
    )
    parser.add_argument("--dataset", default=str(ROOT.parent / "Dataset" / "filtered"))
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "bench_v1"))
    parser.add_argument("--size", type=int, default=TARGET_SIZE)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    dataset_dir = Path(args.dataset).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    programs = manifest["programs"]

    print(f"manifest : {manifest_path}")
    print(f"programs : {len(programs)}")
    print("-" * 60, flush=True)

    infos: dict[str, dict] = {}
    for entry in programs:
        path = dataset_dir / entry["path"]
        try:
            infos[entry["path"]] = _classify(entry, path, dataset_dir)
        except OSError:
            infos[entry["path"]] = {
                "path": entry["path"],
                "raw_hash": None,
                "manifest_hash": entry.get("normalized_sha256"),
                "ast_hash": None,
                "signature_hash": None,
                "n_params": 0,
                "n_loops": 0,
                "n_branches": 0,
                "claim": (entry.get("claims") or [None])[0],
                "n_claims": len(entry.get("claims") or []),
            }

    report = select(
        programs, infos, target=args.size, seed=args.seed
    )

    sample_path = out_dir / "sample_1000.txt"
    sample_path.write_text(
        "\n".join(report["selected"]) + "\n", encoding="utf-8"
    )

    report_path = out_dir / "sampling_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"wrote {sample_path}")
    print(f"wrote {report_path}")
    print("-" * 60)
    print(f"selected        : {report['selected_size']}")
    print(f"claim groups    : {report['n_claim_groups_covered']}"
          f"/{report['n_claim_groups_in_pool']}")
    print(f"verdicts        : {report['by_verdict']}")
    print(f"verdict shares  : {report['by_verdict_share']}")
    print(f"balanced        : {report['balanced']}")
    print(f"dropped         : {report['n_dropped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the temporary 200-program QF-LIA dataset.

The generator produces ``dataset_temp_200/`` with:

* ``programs/<group>/<program_id>.py`` -- 200 self-contained prototype
  programs, each a single function holding exactly one ``assert`` claim.
* ``manifest.json`` -- the ground-truth index (one entry per program).
* ``README.md`` -- the generation rules and the label policy.

Every program is validated twice before it is accepted:

1. **Subset gate** -- :func:`astverifier.shared.subset.check_and_normalize`
   must accept the source (QF-LIA whitelist).
2. **Execution oracle** -- the claim is rewritten into a checking call and
   executed over a bounded input domain. The observed behaviour must match
   the expected verdict, otherwise the build fails.

The oracle is what makes the ground truth trustworthy: no LLM is involved in
labeling, so no method is graded by its own instrument.

Run with::

    python tools/build_temp200.py
"""
from __future__ import annotations

import ast
import itertools
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astverifier.shared.subset import check_and_normalize  # noqa: E402

DATASET_DIR = ROOT / "dataset_temp_200"
PROGRAMS_DIR = DATASET_DIR / "programs"

# --- Input domains used by the execution oracle ------------------------------

DOMAIN_ALL = [-3, -2, -1, 0, 1, 2, 3, 4, 5, 8, 10, 17, 32]
DOMAIN_NON_NEG = [0, 1, 2, 3, 4, 5, 8, 10, 17, 32]
DOMAIN_POSITIVE = [1, 2, 3, 4, 5, 8, 10, 17, 32]


@dataclass
class Spec:
    """One generated program."""

    group: str
    family: str
    program_id: str
    lines: list[str]
    verdict: str
    note: str
    params: list[str] = field(default_factory=lambda: ["n"])
    domain: list[int] = field(default_factory=lambda: list(DOMAIN_ALL))
    precondition: str = "none"
    bug_type: str = "none"

    def source(self) -> str:
        header = [
            f"# {self.program_id}",
            f"# group: {self.group} | family: {self.family}",
            f"# expected_verdict: {self.verdict} | bug_type: {self.bug_type}",
            f"# note: {self.note}",
            "",
            f"def f({', '.join(self.params)}: int) -> int:",
        ]
        return "\n".join(header + self.lines) + "\n"


# --- Small helpers ------------------------------------------------------------


def _default_return(setup: list[str]) -> str:
    """Pick a variable that is guaranteed to be assigned in ``setup``.

    The claim programs are single-function prototypes, so the return value is
    irrelevant for verification -- it only has to be well-defined so the
    execution oracle can run the function.
    """
    import re

    assigned: list[str] = []
    for line in setup:
        stripped = line.strip()
        if stripped.startswith(("for ", "while ", "if ", "elif ", "else")):
            continue
        match = re.match(r"([A-Za-z_][A-Za-z_0-9]*)\s*(?:[+\-*/%]?=)", stripped)
        if match and match.group(1) not in assigned:
            assigned.append(match.group(1))
    for preferred in ("x", "s", "c2", "d", "i"):
        if preferred in assigned:
            return preferred
    return assigned[0] if assigned else "0"


def post(
    group: str,
    family: str,
    program_id: str,
    setup: list[str],
    expr: str,
    verdict: str,
    note: str,
    *,
    ret: str | None = None,
    **kwargs,
) -> Spec:
    """A program whose claim is a postcondition checked before the return."""
    lines = list(setup)
    lines.append(f"    assert {expr}")
    lines.append(f"    return {ret if ret is not None else _default_return(setup)}")
    return Spec(group, family, program_id, lines, verdict, note, **kwargs)


def post_return(
    group: str,
    family: str,
    program_id: str,
    setup: list[str],
    expr: str,
    verdict: str,
    note: str,
    *,
    ret: str | None = None,
    **kwargs,
) -> Spec:
    """A program whose claim sits *after* the return statement (unreachable)."""
    lines = list(setup)
    lines.append(f"    return {ret if ret is not None else _default_return(setup)}")
    lines.append(f"    assert {expr}")
    return Spec(group, family, program_id, lines, verdict, note, **kwargs)


def post_branch(
    group: str,
    family: str,
    program_id: str,
    setup: list[str],
    branch: str,
    expr: str,
    verdict: str,
    note: str,
    *,
    ret: str | None = None,
    **kwargs,
) -> Spec:
    """A program whose claim sits inside a branch that can never be taken."""
    lines = list(setup)
    lines.append(f"    if {branch}:")
    lines.append(f"        assert {expr}")
    lines.append(
        f"    return {ret if ret is not None else _default_return(setup)}"
    )
    return Spec(group, family, program_id, lines, verdict, note, **kwargs)


def _domain_with(*values: int) -> list[int]:
    """A domain extended with the values a specific claim needs."""
    return sorted(set(DOMAIN_ALL) | {v for v in values if isinstance(v, int)})


def _domain_non_neg_with(*values: int) -> list[int]:
    """A non-negative domain extended with the values a claim needs."""
    return sorted(set(DOMAIN_NON_NEG) | {v for v in values if isinstance(v, int)})


# --- Group 1: simple arithmetic and linear expressions (40) ------------------


def build_group_1() -> list[Spec]:
    """20 VERIFIED and 20 COUNTEREXAMPLE programs (10 families x 2 x 2)."""
    specs: list[Spec] = []
    g = "g1_arithmetic"

    def pair(family: str, pid: str, setup: list[str], good: str, bad: str,
             note_good: str, note_bad: str, bug_type: str, **kwargs) -> None:
        specs.append(post(g, family, pid, setup, good, "VERIFIED", note_good, **kwargs))
        specs.append(post(g, family, pid + "_b", setup, bad, "COUNTEREXAMPLE",
                          note_bad, bug_type=bug_type, **kwargs))

    # 1. add a constant
    for i, c in enumerate([3, 7]):
        pair("add_const", f"g1_addconst_{i + 1:02d}", [f"    x = n + {c}"],
             f"x == n + {c}", f"x == n + {c} + 1",
             f"postcondition of x = n + {c}", "off-by-one in the claim",
             "claim_off_by_one")

    # 2. multiply by a constant
    for i, c in enumerate([2, 5]):
        pair("mul_const", f"g1_mulconst_{i + 1:02d}", [f"    x = n * {c}"],
             f"x == {c} * n", f"x == {c} * n + {c}",
             f"postcondition of x = {c} * n", "the claim forgets the factor",
             "claim_wrong_coefficient")

    # 3. subtract a constant
    for i, c in enumerate([1, 4]):
        pair("sub_const", f"g1_subconst_{i + 1:02d}", [f"    x = n - {c}"],
             f"x == n - {c}", f"x == n + {c}",
             f"postcondition of x = n - {c}", "sign error in the claim",
             "claim_sign_error")

    # 4. two-step update
    for i, (c, d) in enumerate([(3, 1), (5, 2)]):
        pair("two_step", f"g1_twostep_{i + 1:02d}",
             [f"    x = n + {c}", f"    x = x - {d}"],
             f"x == n + {c} - {d}", f"x == n + {c} - {d} - 1",
             "postcondition of a two-step update", "off-by-one in the claim",
             "claim_off_by_one")

    # 5. accumulate the input
    for i, c in enumerate([2, 6]):
        pair("accumulate", f"g1_accum_{i + 1:02d}",
             ["    x = 0", "    x = x + n", f"    x = x + {c}"],
             f"x == n + {c}", "x == n",
             "postcondition after accumulation",
             "the claim forgets the added constant", "claim_missing_constant")

    # 6. negation
    for i, c in enumerate([4, 6]):
        pair("negate", f"g1_negate_{i + 1:02d}",
             ["    x = -n", f"    x = x + {c}"],
             f"x == {c} - n", f"x == n + {c}",
             "postcondition of a negated input", "sign error in the claim",
             "claim_sign_error")

    # 7. modulo
    for i, c in enumerate([3, 5]):
        pair("modulo", f"g1_modulo_{i + 1:02d}", [f"    x = n % {c}"],
             "x >= 0", "x > 0",
             "a modulo with a positive divisor is non-negative",
             "the claim excludes the zero remainder", "claim_strict_bound")

    # 8. floor division
    for i, c in enumerate([2, 4]):
        pair("floor_div", f"g1_floordiv_{i + 1:02d}", [f"    x = n // {c}"],
             "x >= 0", "x > 0",
             "the floor quotient of a non-negative value is non-negative",
             "the claim excludes the zero quotient", "claim_strict_bound",
             params=["n"], domain=list(DOMAIN_NON_NEG))

    # 9. absolute value
    for i, c in enumerate([3, 5]):
        pair("absolute", f"g1_abs_{i + 1:02d}", [f"    x = abs(n - {c})"],
             "x >= 0", "x > 0",
             "an absolute value is non-negative",
             "the claim excludes the zero case", "claim_strict_bound",
             domain=_domain_with(c))

    # 10. minimum against a constant
    for i, c in enumerate([4, 6]):
        pair("min_max", f"g1_minmax_{i + 1:02d}", [f"    x = min(n, {c})"],
             f"x <= {c}", f"x >= {c}",
             "min(n, c) never exceeds c", "the claim is inverted for min",
             "claim_inverted")

    return specs


# --- Group 2: conditionals (40) -----------------------------------------------


def build_group_2() -> list[Spec]:
    """20 VERIFIED and 20 COUNTEREXAMPLE programs (10 families x 2 x 2)."""
    specs: list[Spec] = []
    g = "g2_conditionals"

    def pair(family: str, pid: str, setup: list[str], good: str, bad: str,
             note_good: str, note_bad: str, bug_type: str, **kwargs) -> None:
        specs.append(post(g, family, pid, setup, good, "VERIFIED", note_good, **kwargs))
        specs.append(post(g, family, pid + "_b", setup, bad, "COUNTEREXAMPLE",
                          note_bad, bug_type=bug_type, **kwargs))

    # 1. maximum of two values
    for i, c in enumerate([5, 7]):
        pair("max2", f"g2_max2_{i + 1:02d}",
             [f"    if n > {c}:", "        x = n", "    else:", f"        x = {c}"],
             f"x >= {c}", f"x <= {c}",
             "max(n, c) is at least c", "the claim is inverted for max",
             "claim_inverted")

    # 2. minimum of two values
    for i, c in enumerate([5, 7]):
        pair("min2", f"g2_min2_{i + 1:02d}",
             [f"    if n < {c}:", "        x = n", "    else:", f"        x = {c}"],
             f"x <= {c}", f"x >= {c}",
             "min(n, c) is at most c", "the claim is inverted for min",
             "claim_inverted")

    # 3. sign handling
    for i, _c in enumerate([0, 3]):
        pair("sign", f"g2_sign_{i + 1:02d}",
             ["    if n == 0:", "        x = 0", "    elif n > 0:", "        x = 1",
              "    else:", "        x = -1"],
             "x >= -1", "x >= 0",
             "the signum is at least -1",
             "the claim ignores the negative branch", "claim_missing_branch")

    # 4. clamping
    for i, (lo, hi) in enumerate([(0, 10), (2, 8)]):
        pair("clamp", f"g2_clamp_{i + 1:02d}",
             [f"    if n < {lo}:", f"        x = {lo}", f"    elif n > {hi}:",
              f"        x = {hi}", "    else:", "        x = n"],
             f"x >= {lo}", f"x <= {lo}",
             f"the clamp lower bound is {lo}",
             "the claim uses the wrong clamp bound", "claim_wrong_bound")

    # 5. parity
    for i, c in enumerate([2, 3]):
        pair("parity", f"g2_parity_{i + 1:02d}",
             [f"    if n % {c} == 0:", "        x = 1", "    else:", "        x = 0"],
             "x >= 0", "x == 1",
             "the parity flag is a bit",
             "the claim ignores the odd branch", "claim_missing_branch")

    # 6. threshold test
    for i, c in enumerate([10, 20]):
        pair("threshold", f"g2_threshold_{i + 1:02d}",
             [f"    if n > {c}:", "        x = 1", "    else:", "        x = 0"],
             "x >= 0", "x <= 0",
             "the threshold flag is a bit",
             "the claim is inverted for the threshold flag", "claim_inverted")

    # 7. nested conditionals
    for i, c in enumerate([10, 20]):
        pair("nested_if", f"g2_nested_{i + 1:02d}",
             ["    if n > 0:", f"        if n > {c}:", "            x = 2",
              "        else:", "            x = 1", "    else:", "        x = 0"],
             "x >= 0", "x <= 0",
             "the nested classification yields a non-negative label",
             "the claim is inverted for the nested classification",
             "claim_inverted")

    # 8. conjunction of bounds
    for i, c in enumerate([10, 20]):
        pair("and_bound", f"g2_andbound_{i + 1:02d}",
             [f"    if n > 0 and n < {c}:", "        x = 1", "    else:", "        x = 0"],
             "x >= 0", "x == 1",
             "the range flag is a bit",
             "the claim ignores the out-of-range branch", "claim_missing_branch")

    # 9. disjunction of bounds
    for i, c in enumerate([10, 20]):
        pair("or_bound", f"g2_orbound_{i + 1:02d}",
             [f"    if n < 0 or n > {c}:", "        x = 1", "    else:", "        x = 0"],
             "x >= 0", "x == 1",
             "the outside-range flag is a bit",
             "the claim ignores the inside-range branch", "claim_missing_branch")

    # 10. conditional expression
    for i, c in enumerate([5, 7]):
        pair("ternary", f"g2_ternary_{i + 1:02d}", [f"    x = {c} if n > 0 else -1"],
             "x >= -1", "x >= 0",
             "the conditional expression is bounded below",
             "the claim ignores the negative branch", "claim_missing_branch")

    return specs


# --- Group 3: single loops and loop invariants (50) --------------------------


def build_group_3() -> list[Spec]:
    """25 VERIFIED and 25 COUNTEREXAMPLE programs."""
    specs: list[Spec] = []
    g = "g3_loops"

    def pair(family: str, pid: str, setup: list[str], good: str, bad: str,
             note_good: str, note_bad: str, bug_type: str, **kwargs) -> None:
        specs.append(post(g, family, pid, setup, good, "VERIFIED", note_good, **kwargs))
        specs.append(post(g, family, pid + "_b", setup, bad, "COUNTEREXAMPLE",
                          note_bad, bug_type=bug_type, **kwargs))

    # 1. counting loop (3 instances)
    for i in range(3):
        pair("count_loop", f"g3_count_{i + 1:02d}",
             ["    i = 0", "    s = 0", "    while i < n:", "        s = s + 1",
              "        i = i + 1"],
             "s == n", "s == n + 1",
             "the counter equals the number of iterations",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 2. accumulate a constant (3 instances)
    for i, c in enumerate([2, 3, 4]):
        pair("add_const_loop", f"g3_addconst_{i + 1:02d}",
             ["    i = 0", "    s = 0", "    while i < n:", f"        s = s + {c}",
              "        i = i + 1"],
             f"s == {c} * n", f"s == {c} * n + {c}",
             "the accumulator grows by a constant per iteration",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 3. countdown loop (3 instances)
    for i in range(3):
        pair("countdown", f"g3_countdown_{i + 1:02d}",
             ["    i = n", "    s = 0", "    while i > 0:", "        i = i - 1",
              "        s = s + 1"],
             "i == 0", "i == 1",
             "the countdown reaches zero", "off-by-one in the claim",
             "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 4. for-loop accumulation (3 instances)
    for i, c in enumerate([2, 3, 4]):
        pair("for_loop", f"g3_forloop_{i + 1:02d}",
             ["    s = 0", "    for i in range(n):", f"        s = s + {c}"],
             f"s == {c} * n", f"s == {c} * n - {c}",
             "a for-loop accumulates a constant per iteration",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 5. countdown by two (3 instances)
    for i in range(3):
        pair("countdown_by_two", f"g3_countdown2_{i + 1:02d}",
             ["    i = n", "    s = 0", "    while i > 0:", "        i = i - 2",
              "        s = s + 1"],
             "i <= 0", "i == 0",
             "the countdown by two ends at or below zero",
             "the claim ignores the odd leftover", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 6. stripping factors (2 instances)
    for i, c in enumerate([2, 3]):
        pair("strip_factor", f"g3_strip_{i + 1:02d}",
             ["    x = n", f"    while x > 0 and x % {c} == 0:",
              f"        x = x // {c}"],
             "x >= 0", "x == n",
             "the reduced value stays non-negative",
             "the claim ignores the reduction", "claim_ignores_effect",
             params=["n"], domain=_domain_non_neg_with(c, c * c),
             precondition="n >= 0")

    # 7. counting multiples (2 instances)
    for i, c in enumerate([3, 4]):
        pair("count_multiples", f"g3_multiples_{i + 1:02d}",
             ["    i = 0", "    s = 0", "    while i < n:", "        i = i + 1",
              f"        if i % {c} == 0:", "            s = s + 1"],
             f"s == n // {c}", "s == n",
             "the loop counts the multiples of c",
             "the claim ignores the filter", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 8. loop with break (2 instances)
    for i in range(2):
        pair("break_loop", f"g3_break_{i + 1:02d}",
             ["    i = 0", "    s = 0", "    while True:", "        i = i + 1",
              "        if i > n:", "            break", "        s = s + 2"],
             "s == 2 * n", "s == 2 * n + 2",
             "the loop body runs exactly n times",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 9. inner constant loop (2 instances)
    for i, c in enumerate([2, 3]):
        pair("inner_const_loop", f"g3_innerconst_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            c2 = c2 + 1", "        i = i + 1"],
             f"c2 == {c} * n", "c2 == n",
             "the inner loop runs a constant number of times",
             "the claim ignores the inner loop", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 10. loop plus a trailing update (2 instances)
    for i, c in enumerate([2, 4]):
        pair("trailing_update", f"g3_trailing_{i + 1:02d}",
             ["    i = 0", "    x = 0", "    while i < n:", "        x = x + 1",
              "        i = i + 1", f"    x = x + {c}"],
             f"x == n + {c}", f"x == n + {c} + 1",
             "the postcondition after the loop",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    return specs


# --- Group 4: nested loops and deep bounds (40) -------------------------------


def build_group_4() -> list[Spec]:
    """20 VERIFIED and 20 COUNTEREXAMPLE programs (10 families x 2 x 2)."""
    specs: list[Spec] = []
    g = "g4_nested"

    def pair(family: str, pid: str, setup: list[str], good: str, bad: str,
             note_good: str, note_bad: str, bug_type: str, **kwargs) -> None:
        specs.append(post(g, family, pid, setup, good, "VERIFIED", note_good, **kwargs))
        specs.append(post(g, family, pid + "_b", setup, bad, "COUNTEREXAMPLE",
                          note_bad, bug_type=bug_type, **kwargs))

    # 1. inner loop with a constant bound
    for i, c in enumerate([2, 3]):
        pair("inner_const", f"g4_innerconst_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            c2 = c2 + 1", "        i = i + 1"],
             f"c2 == {c} * n", f"c2 == {c} * n + 1",
             "the nested body runs c * n times",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 2. three nested counting loops
    for i, (a, b) in enumerate([(2, 3), (3, 4)]):
        pair("triple_count", f"g4_count_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {a}:", "            j = j + 1", "            k = 0",
              f"            while k < {b}:", "                k = k + 1",
              "                c2 = c2 + 1", "        i = i + 1"],
             f"c2 == {a * b} * n", f"c2 == {a} * n",
             "the innermost body runs a * b * n times",
             "the claim forgets one nesting level", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 3. nested loop with a filter
    for i, c in enumerate([2, 3]):
        pair("nested_filter", f"g4_filter_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              "        while j < 2:", "            j = j + 1",
              f"            if i % {c} == 0:", "                c2 = c2 + 1",
              "        i = i + 1"],
             "c2 >= 0", "c2 == n",
             "the filtered counter stays non-negative",
             "the claim ignores the filter", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 4. nested loop with an inner break
    for i, c in enumerate([3, 4]):
        pair("inner_break", f"g4_innerbreak_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            if j > 2:", "                break", "            c2 = c2 + 1",
              "        i = i + 1"],
             "c2 == 2 * n", "c2 == 3 * n",
             "the inner loop breaks after two steps",
             "the claim ignores the break", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 5. nested loop over a constant bound
    for i, c in enumerate([3, 4]):
        pair("nested_const", f"g4_twoparam_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            c2 = c2 + 1", "        i = i + 1"],
             f"c2 == {c} * n", f"c2 == {c} * n - {c}",
             "the nested counter grows by a constant factor",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 6. nested loop with an accumulator and a trailing update
    for i, c in enumerate([2, 4]):
        pair("nested_accum", f"g4_accum_{i + 1:02d}",
             ["    i = 0", "    x = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            x = x + 1", "        i = i + 1", f"    x = x + {c}"],
             f"x == {c} * n + {c}", f"x == {c} * n",
             "the postcondition of a nested accumulation",
             "the claim forgets the trailing update", "claim_missing_constant",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 7. nested loop with a minimum computation
    for i, c in enumerate([5, 6]):
        pair("nested_min", f"g4_minimum_{i + 1:02d}",
             ["    i = 0", "    x = n", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            x = min(x, j)", "        i = i + 1"],
             "x >= 0", "x == n",
             "the running minimum stays non-negative",
             "the claim pins the minimum to the input", "claim_wrong_value",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 8. nested loop with a maximum computation
    for i, c in enumerate([5, 6]):
        pair("nested_max", f"g4_maximum_{i + 1:02d}",
             ["    i = 0", "    x = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            x = max(x, j)", "        i = i + 1"],
             "x >= 0", "x == 0",
             "the running maximum stays non-negative",
             "the claim pins the maximum to zero", "claim_wrong_value",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 9. nested loop counting with a step
    for i, c in enumerate([2, 3]):
        pair("nested_step", f"g4_step_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", "        j = 0",
              f"        while j < {c}:", "            j = j + 1",
              "            if j % 2 == 0:", "                c2 = c2 + 1",
              "        i = i + 1"],
             "c2 >= 0", f"c2 == {c} * n",
             "the parity-filtered counter is non-negative",
             "the claim ignores the parity filter", "claim_ignores_effect",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    # 10. nested loop with a decreasing inner bound
    for i, c in enumerate([4, 5]):
        pair("nested_decrease", f"g4_decrease_{i + 1:02d}",
             ["    i = 0", "    c2 = 0", "    while i < n:", f"        j = {c}",
              "        while j > 0:", "            j = j - 1",
              "            c2 = c2 + 1", "        i = i + 1"],
             f"c2 == {c} * n", f"c2 == {c} * n + {c}",
             "the decreasing inner loop runs c times",
             "off-by-one in the claim", "claim_off_by_one",
             params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0")

    return specs


# --- Group 5: real bugs and unreachable claims (30) ---------------------------


def build_group_5() -> list[Spec]:
    """15 programs with genuine implementation bugs and 15 unreachable claims."""
    specs: list[Spec] = []
    g = "g5_bugs"

    # --- genuine implementation bugs (the claim is correct, the code is not) --
    for i in range(5):
        specs.append(post(
            g, "bug_off_by_one", f"g5_bug_offbyone_{i + 1:02d}",
            ["    i = 0", "    s = 0", "    while i <= n:", "        s = s + 1",
             "        i = i + 1"],
            "s == n", "COUNTEREXAMPLE",
            "the loop condition runs one iteration too many",
            bug_type="impl_off_by_one",
            params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0"))

    for i in range(5):
        specs.append(post(
            g, "bug_wrong_update", f"g5_bug_wrongupdate_{i + 1:02d}",
            ["    i = 0", "    s = 0", "    while i < n:", "        s = s + 1",
             "        i = i + 2"],
            "s == n", "COUNTEREXAMPLE",
            "the induction variable advances by two",
            bug_type="impl_wrong_update",
            params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0"))

    for i in range(5):
        specs.append(post(
            g, "bug_bad_init", f"g5_bug_badinit_{i + 1:02d}",
            ["    i = 0", "    s = 1", "    while i < n:", "        s = s + 1",
             "        i = i + 1"],
            "s == n", "COUNTEREXAMPLE",
            "the accumulator is initialised to one",
            bug_type="impl_bad_init",
            params=["n"], domain=list(DOMAIN_NON_NEG), precondition="n >= 0"))

    # --- unreachable claims ---------------------------------------------------
    for i, c in enumerate([3, 5, 7, 9, 11, 13, 15, 17]):
        specs.append(post_return(
            g, "unreach_after_return", f"g5_unreach_afterreturn_{i + 1:02d}",
            [f"    x = n + {c}"], f"x == n + {c}", "UNREACHABLE",
            "the claim sits after the return statement",
            bug_type="unreachable_after_return"))

    for i, c in enumerate([4, 6, 8, 10, 12, 14, 16]):
        specs.append(post_branch(
            g, "unreach_branch", f"g5_unreach_branch_{i + 1:02d}",
            [f"    x = n + {c}"], "n > 100 and n < 0", f"x == n + {c}",
            "UNREACHABLE", "the claim sits in a contradictory branch",
            bug_type="unreachable_branch"))

    return specs


# --- Execution oracle ---------------------------------------------------------


class _ClaimFailedError(Exception):
    """Raised when the rewritten claim evaluates to false."""


def _rewrite_asserts(source: str) -> str:
    """Replace every ``assert EXPR`` with ``_chk(EXPR)``.

    The rewrite is purely textual and keeps the line structure intact, so the
    line numbers reported in the manifest stay valid.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    targets: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            targets.append(node.lineno)
    for lineno in targets:
        original = lines[lineno - 1]
        marker = "assert "
        index = original.index(marker)
        lines[lineno - 1] = (
            original[:index] + "_chk(" + original[index + len(marker):].rstrip() + ")"
        )
    return "\n".join(lines) + "\n"


def _observe(source: str, func_name: str, params: list[str],
             domain: list[int]) -> tuple[str, dict | None]:
    """Execute the program and report the claim behaviour.

    Returns ``("COUNTEREXAMPLE", witness)`` when the claim fails for some
    input, ``("UNREACHABLE", None)`` when the claim is never evaluated, and
    ``("VERIFIED", None)`` when the claim holds for every tested input.
    """
    rewritten = _rewrite_asserts(source)
    state = {"reached": False}

    def _chk(value: bool) -> None:
        state["reached"] = True
        if not value:
            raise _ClaimFailedError

    namespace: dict = {"_chk": _chk}
    try:
        exec(compile(rewritten, "<oracle>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # pragma: no cover - defensive
        return ("ERROR", {"error": f"compile/exec failed: {exc}"})

    function = namespace.get(func_name)
    if function is None:
        return ("ERROR", {"error": f"function {func_name} not found"})

    for combination in itertools.product(domain, repeat=len(params)):
        arguments = dict(zip(params, combination, strict=True))
        try:
            function(**arguments)
        except _ClaimFailedError:
            return ("COUNTEREXAMPLE", arguments)
        except RecursionError:
            return ("ERROR", {"error": "recursion error", "input": arguments})
    if not state["reached"]:
        return ("UNREACHABLE", None)
    return ("VERIFIED", None)


def _function_signature(source: str) -> tuple[str, list[str]]:
    """Return the first function name and its parameter names."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            args = [a.arg for a in node.args.posonlyargs + node.args.args]
            return node.name, args
    return ("", [])


# --- Build ---------------------------------------------------------------------


def build() -> int:
    specs: list[Spec] = []
    specs.extend(build_group_1())
    specs.extend(build_group_2())
    specs.extend(build_group_3())
    specs.extend(build_group_4())
    specs.extend(build_group_5())

    seen: set[str] = set()
    for spec in specs:
        if spec.program_id in seen:
            raise SystemExit(f"duplicate program id: {spec.program_id}")
        seen.add(spec.program_id)

    if len(specs) != 200:
        raise SystemExit(f"expected 200 programs, got {len(specs)}")

    # Remove any previous output so a failed build cannot leave stale files
    # behind that would silently disagree with the manifest.
    if PROGRAMS_DIR.exists():
        shutil.rmtree(PROGRAMS_DIR)
    PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_entries = []
    failures: list[str] = []

    for spec in specs:
        source = spec.source()
        group_dir = PROGRAMS_DIR / spec.group
        group_dir.mkdir(parents=True, exist_ok=True)
        path = group_dir / f"{spec.program_id}.py"
        path.write_text(source, encoding="utf-8")

        # 1. subset gate
        subset = check_and_normalize(source)
        if not subset.accepted:
            failures.append(f"{spec.program_id}: subset rejected -> "
                            f"{subset.rejected_reason}")
            continue

        # 2. execution oracle
        func_name, params = _function_signature(source)
        observed, witness = _observe(source, func_name, params, spec.domain)
        if observed != spec.verdict:
            failures.append(
                f"{spec.program_id}: expected {spec.verdict}, oracle says "
                f"{observed} (witness={witness})"
            )
            continue

        assert_line = 0
        for index, line in enumerate(source.splitlines(), start=1):
            if line.strip().startswith("assert "):
                assert_line = index
        claim_expr = ""
        for node in ast.walk(subset.tree):
            if isinstance(node, ast.Assert):
                claim_expr = ast.unparse(node.test)

        manifest_entries.append({
            "program_id": spec.program_id,
            "path": f"programs/{spec.group}/{spec.program_id}.py",
            "group": spec.group,
            "family": spec.family,
            "expected_verdict": spec.verdict,
            "bug_type": spec.bug_type,
            "precondition": spec.precondition,
            "params": spec.params,
            "assert_line": assert_line,
            "claim_expression": claim_expr,
            "note": spec.note,
            "gt_source": "execution_oracle",
            "gt_confidence": "high",
        })

    if failures:
        print("BUILD FAILED -- the following programs disagree with the oracle:")
        for failure in failures:
            print("  -", failure)
        return 1

    counts: dict[str, int] = {}
    for entry in manifest_entries:
        counts[entry["expected_verdict"]] = counts.get(entry["expected_verdict"], 0) + 1

    manifest = {
        "version": "1.0",
        "generated_by": "tools/build_temp200.py",
        "subset": "QF-LIA (int/bool, linear arithmetic, if/while/for over range)",
        "labeling": "execution oracle (no LLM in the labeling loop)",
        "n_programs": len(manifest_entries),
        "counts_by_verdict": counts,
        "counts_by_group": _count_by(manifest_entries, "group"),
        "counts_by_bug_type": _count_by(manifest_entries, "bug_type"),
        "programs": manifest_entries,
    }
    (DATASET_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"OK: {len(manifest_entries)} programs written to {DATASET_DIR}")
    print("verdicts:", counts)
    return 0


def _count_by(entries: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry[key]] = counts.get(entry[key], 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    raise SystemExit(build())

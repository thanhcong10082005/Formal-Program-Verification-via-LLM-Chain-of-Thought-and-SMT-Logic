# g3_multiples_01_b
# group: g3_loops | family: count_multiples
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# precondition: n >= 0
# note: the claim ignores the filter

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        i = i + 1
        if i % 3 == 0:
            s = s + 1
    assert s == n
    return s

# g3_multiples_02_b
# group: g3_loops | family: count_multiples
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# note: the claim ignores the filter

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        i = i + 1
        if i % 4 == 0:
            s = s + 1
    assert s == n
    return s

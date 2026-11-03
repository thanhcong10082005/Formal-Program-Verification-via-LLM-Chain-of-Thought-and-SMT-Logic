# g3_strip_02_b
# group: g3_loops | family: strip_factor
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# precondition: n >= 0
# note: the claim ignores the reduction

def f(n: int) -> int:
    x = n
    while x > 0 and x % 3 == 0:
        x = x // 3
    assert x == n
    return x

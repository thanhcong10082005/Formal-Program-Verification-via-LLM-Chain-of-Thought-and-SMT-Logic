# g3_strip_01_b
# group: g3_loops | family: strip_factor
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# note: the claim ignores the reduction

def f(n: int) -> int:
    x = n
    while x > 0 and x % 2 == 0:
        x = x // 2
    assert x == n
    return x

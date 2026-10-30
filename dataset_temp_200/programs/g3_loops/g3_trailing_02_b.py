# g3_trailing_02_b
# group: g3_loops | family: trailing_update
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        x = x + 1
        i = i + 1
    x = x + 4
    assert x == n + 4 + 1
    return x

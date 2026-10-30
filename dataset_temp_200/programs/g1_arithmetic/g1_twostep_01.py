# g1_twostep_01
# group: g1_arithmetic | family: two_step
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of a two-step update

def f(n: int) -> int:
    x = n + 3
    x = x - 1
    assert x == n + 3 - 1
    return x

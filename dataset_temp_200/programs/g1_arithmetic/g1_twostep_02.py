# g1_twostep_02
# group: g1_arithmetic | family: two_step
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: postcondition of a two-step update

def f(n: int) -> int:
    x = n + 5
    x = x - 2
    assert x == n + 5 - 2
    return x

# g1_addconst_01
# group: g1_arithmetic | family: add_const
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: postcondition of x = n + 3

def f(n: int) -> int:
    x = n + 3
    assert x == n + 3
    return x

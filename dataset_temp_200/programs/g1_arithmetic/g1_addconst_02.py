# g1_addconst_02
# group: g1_arithmetic | family: add_const
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of x = n + 7

def f(n: int) -> int:
    x = n + 7
    assert x == n + 7
    return x

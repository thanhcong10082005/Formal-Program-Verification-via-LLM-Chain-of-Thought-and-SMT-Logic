# g1_subconst_02
# group: g1_arithmetic | family: sub_const
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: postcondition of x = n - 4

def f(n: int) -> int:
    x = n - 4
    assert x == n - 4
    return x

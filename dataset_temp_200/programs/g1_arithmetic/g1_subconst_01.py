# g1_subconst_01
# group: g1_arithmetic | family: sub_const
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: postcondition of x = n - 1

def f(n: int) -> int:
    x = n - 1
    assert x == n - 1
    return x

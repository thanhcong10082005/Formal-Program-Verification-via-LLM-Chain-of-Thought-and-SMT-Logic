# g1_accum_02
# group: g1_arithmetic | family: accumulate
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition after accumulation

def f(n: int) -> int:
    x = 0
    x = x + n
    x = x + 6
    assert x == n + 6
    return x

# g1_negate_02
# group: g1_arithmetic | family: negate
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of a negated input

def f(n: int) -> int:
    x = -n
    x = x + 6
    assert x == 6 - n
    return x

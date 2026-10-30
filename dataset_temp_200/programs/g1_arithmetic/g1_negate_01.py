# g1_negate_01
# group: g1_arithmetic | family: negate
# expected_verdict: VERIFIED | bug_type: none
# note: postcondition of a negated input

def f(n: int) -> int:
    x = -n
    x = x + 4
    assert x == 4 - n
    return x

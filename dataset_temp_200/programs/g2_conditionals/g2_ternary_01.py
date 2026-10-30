# g2_ternary_01
# group: g2_conditionals | family: ternary
# expected_verdict: VERIFIED | bug_type: none
# note: the conditional expression is bounded below

def f(n: int) -> int:
    x = 5 if n > 0 else -1
    assert x >= -1
    return x

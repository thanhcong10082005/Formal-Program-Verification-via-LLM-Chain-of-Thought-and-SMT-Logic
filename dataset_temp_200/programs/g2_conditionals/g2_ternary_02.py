# g2_ternary_02
# group: g2_conditionals | family: ternary
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the conditional expression is bounded below

def f(n: int) -> int:
    x = 7 if n > 0 else -1
    assert x >= -1
    return x

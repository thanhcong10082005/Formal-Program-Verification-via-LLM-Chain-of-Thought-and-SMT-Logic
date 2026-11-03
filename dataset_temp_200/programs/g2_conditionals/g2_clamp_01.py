# g2_clamp_01
# group: g2_conditionals | family: clamp
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the clamp lower bound is 0

def f(n: int) -> int:
    if n < 0:
        x = 0
    elif n > 10:
        x = 10
    else:
        x = n
    assert x >= 0
    return x

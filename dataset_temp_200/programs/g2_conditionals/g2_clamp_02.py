# g2_clamp_02
# group: g2_conditionals | family: clamp
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the clamp lower bound is 2

def f(n: int) -> int:
    if n < 2:
        x = 2
    elif n > 8:
        x = 8
    else:
        x = n
    assert x >= 2
    return x

# g2_threshold_01
# group: g2_conditionals | family: threshold
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the threshold flag is a bit

def f(n: int) -> int:
    if n > 10:
        x = 1
    else:
        x = 0
    assert x >= 0
    return x

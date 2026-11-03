# g2_sign_01
# group: g2_conditionals | family: sign
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the signum is at least -1

def f(n: int) -> int:
    if n == 0:
        x = 0
    elif n > 0:
        x = 1
    else:
        x = -1
    assert x >= -1
    return x

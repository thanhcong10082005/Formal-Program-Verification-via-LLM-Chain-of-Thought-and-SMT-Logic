# g2_max2_02
# group: g2_conditionals | family: max2
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: max(n, c) is at least c

def f(n: int) -> int:
    if n > 7:
        x = n
    else:
        x = 7
    assert x >= 7
    return x

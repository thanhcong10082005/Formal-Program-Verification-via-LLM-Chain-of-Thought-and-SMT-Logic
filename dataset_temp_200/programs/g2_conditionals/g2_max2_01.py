# g2_max2_01
# group: g2_conditionals | family: max2
# expected_verdict: VERIFIED | bug_type: none
# note: max(n, c) is at least c

def f(n: int) -> int:
    if n > 5:
        x = n
    else:
        x = 5
    assert x >= 5
    return x

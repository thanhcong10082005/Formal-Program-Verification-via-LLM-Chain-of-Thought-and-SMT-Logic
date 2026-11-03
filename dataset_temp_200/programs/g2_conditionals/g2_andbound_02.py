# g2_andbound_02
# group: g2_conditionals | family: and_bound
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the range flag is a bit

def f(n: int) -> int:
    if n > 0 and n < 20:
        x = 1
    else:
        x = 0
    assert x >= 0
    return x

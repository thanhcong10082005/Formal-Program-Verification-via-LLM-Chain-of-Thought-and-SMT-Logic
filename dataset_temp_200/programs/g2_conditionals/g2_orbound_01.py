# g2_orbound_01
# group: g2_conditionals | family: or_bound
# expected_verdict: VERIFIED | bug_type: none
# note: the outside-range flag is a bit

def f(n: int) -> int:
    if n < 0 or n > 10:
        x = 1
    else:
        x = 0
    assert x >= 0
    return x

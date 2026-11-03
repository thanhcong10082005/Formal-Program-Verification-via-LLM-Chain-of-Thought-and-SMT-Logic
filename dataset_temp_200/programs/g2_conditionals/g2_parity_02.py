# g2_parity_02
# group: g2_conditionals | family: parity
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the parity flag is a bit

def f(n: int) -> int:
    if n % 3 == 0:
        x = 1
    else:
        x = 0
    assert x >= 0
    return x

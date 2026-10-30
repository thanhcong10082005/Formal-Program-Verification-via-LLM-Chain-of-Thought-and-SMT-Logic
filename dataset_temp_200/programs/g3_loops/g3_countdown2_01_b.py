# g3_countdown2_01_b
# group: g3_loops | family: countdown_by_two
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: the claim ignores the odd leftover

def f(n: int) -> int:
    i = n
    s = 0
    while i > 0:
        i = i - 2
        s = s + 1
    assert i == 0
    return s

# g3_countdown_03_b
# group: g3_loops | family: countdown
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = n
    s = 0
    while i > 0:
        i = i - 1
        s = s + 1
    assert i == 1
    return s

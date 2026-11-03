# g3_countdown2_01
# group: g3_loops | family: countdown_by_two
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the countdown by two ends at or below zero

def f(n: int) -> int:
    i = n
    s = 0
    while i > 0:
        i = i - 2
        s = s + 1
    assert i <= 0
    return s

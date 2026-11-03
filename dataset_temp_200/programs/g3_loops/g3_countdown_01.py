# g3_countdown_01
# group: g3_loops | family: countdown
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the countdown reaches zero

def f(n: int) -> int:
    i = n
    s = 0
    while i > 0:
        i = i - 1
        s = s + 1
    assert i == 0
    return s

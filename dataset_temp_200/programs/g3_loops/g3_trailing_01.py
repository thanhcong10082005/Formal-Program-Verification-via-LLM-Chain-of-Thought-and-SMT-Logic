# g3_trailing_01
# group: g3_loops | family: trailing_update
# expected_verdict: VERIFIED | bug_type: none
# note: the postcondition after the loop

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        x = x + 1
        i = i + 1
    x = x + 2
    assert x == n + 2
    return x

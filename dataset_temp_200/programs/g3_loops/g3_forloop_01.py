# g3_forloop_01
# group: g3_loops | family: for_loop
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: a for-loop accumulates a constant per iteration

def f(n: int) -> int:
    s = 0
    for i in range(n):
        s = s + 2
    assert s == 2 * n
    return s

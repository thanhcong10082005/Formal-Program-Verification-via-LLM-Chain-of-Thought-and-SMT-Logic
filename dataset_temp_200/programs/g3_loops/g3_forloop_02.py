# g3_forloop_02
# group: g3_loops | family: for_loop
# expected_verdict: VERIFIED | bug_type: none
# note: a for-loop accumulates a constant per iteration

def f(n: int) -> int:
    s = 0
    for i in range(n):
        s = s + 3
    assert s == 3 * n
    return s

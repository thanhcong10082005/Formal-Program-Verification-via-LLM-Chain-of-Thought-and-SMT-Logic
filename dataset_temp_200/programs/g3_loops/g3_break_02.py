# g3_break_02
# group: g3_loops | family: break_loop
# expected_verdict: VERIFIED | bug_type: none
# note: the loop body runs exactly n times

def f(n: int) -> int:
    i = 0
    s = 0
    while True:
        i = i + 1
        if i > n:
            break
        s = s + 2
    assert s == 2 * n
    return s

# g3_count_03
# group: g3_loops | family: count_loop
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the counter equals the number of iterations

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n
    return s

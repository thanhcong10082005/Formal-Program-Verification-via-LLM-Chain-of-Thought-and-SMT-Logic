# g3_addconst_01
# group: g3_loops | family: add_const_loop
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the accumulator grows by a constant per iteration

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 2
        i = i + 1
    assert s == 2 * n
    return s

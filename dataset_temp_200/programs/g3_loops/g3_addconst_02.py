# g3_addconst_02
# group: g3_loops | family: add_const_loop
# expected_verdict: VERIFIED | bug_type: none
# note: the accumulator grows by a constant per iteration

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 3
        i = i + 1
    assert s == 3 * n
    return s

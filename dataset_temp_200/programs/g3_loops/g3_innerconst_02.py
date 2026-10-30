# g3_innerconst_02
# group: g3_loops | family: inner_const_loop
# expected_verdict: VERIFIED | bug_type: none
# note: the inner loop runs a constant number of times

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 3:
            j = j + 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == 3 * n
    return c2

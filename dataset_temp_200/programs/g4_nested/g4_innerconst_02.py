# g4_innerconst_02
# group: g4_nested | family: inner_const
# expected_verdict: VERIFIED | bug_type: none
# note: the nested body runs c * n times

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

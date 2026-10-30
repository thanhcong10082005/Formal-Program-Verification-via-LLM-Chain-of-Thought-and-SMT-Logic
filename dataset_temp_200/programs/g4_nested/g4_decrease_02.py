# g4_decrease_02
# group: g4_nested | family: nested_decrease
# expected_verdict: VERIFIED | bug_type: none
# note: the decreasing inner loop runs c times

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 5
        while j > 0:
            j = j - 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == 5 * n
    return c2

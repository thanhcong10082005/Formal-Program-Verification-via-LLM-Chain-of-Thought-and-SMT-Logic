# g4_innerbreak_02
# group: g4_nested | family: inner_break
# expected_verdict: VERIFIED | bug_type: none
# note: the inner loop breaks after two steps

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 4:
            j = j + 1
            if j > 2:
                break
            c2 = c2 + 1
        i = i + 1
    assert c2 == 2 * n
    return c2

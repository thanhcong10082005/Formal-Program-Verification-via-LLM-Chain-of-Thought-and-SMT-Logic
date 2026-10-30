# g4_count_02
# group: g4_nested | family: triple_count
# expected_verdict: VERIFIED | bug_type: none
# note: the innermost body runs a * b * n times

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 3:
            j = j + 1
            k = 0
            while k < 4:
                k = k + 1
                c2 = c2 + 1
        i = i + 1
    assert c2 == 12 * n
    return c2

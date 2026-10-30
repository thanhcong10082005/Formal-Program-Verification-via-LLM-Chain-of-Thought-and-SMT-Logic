# g4_accum_01
# group: g4_nested | family: nested_accum
# expected_verdict: VERIFIED | bug_type: none
# note: the postcondition of a nested accumulation

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        j = 0
        while j < 2:
            j = j + 1
            x = x + 1
        i = i + 1
    x = x + 2
    assert x == 2 * n + 2
    return x

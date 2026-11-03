# g4_accum_02
# group: g4_nested | family: nested_accum
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the postcondition of a nested accumulation

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        j = 0
        while j < 4:
            j = j + 1
            x = x + 1
        i = i + 1
    x = x + 4
    assert x == 4 * n + 4
    return x

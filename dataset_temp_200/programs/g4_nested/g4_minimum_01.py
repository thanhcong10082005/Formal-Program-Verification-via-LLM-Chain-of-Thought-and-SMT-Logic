# g4_minimum_01
# group: g4_nested | family: nested_min
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the running minimum stays non-negative

def f(n: int) -> int:
    i = 0
    x = n
    while i < n:
        j = 0
        while j < 5:
            j = j + 1
            x = min(x, j)
        i = i + 1
    assert x >= 0
    return x

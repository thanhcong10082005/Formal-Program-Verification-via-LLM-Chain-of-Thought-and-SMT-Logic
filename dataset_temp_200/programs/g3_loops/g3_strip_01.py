# g3_strip_01
# group: g3_loops | family: strip_factor
# expected_verdict: VERIFIED | bug_type: none
# note: the reduced value stays non-negative

def f(n: int) -> int:
    x = n
    while x > 0 and x % 2 == 0:
        x = x // 2
    assert x >= 0
    return x

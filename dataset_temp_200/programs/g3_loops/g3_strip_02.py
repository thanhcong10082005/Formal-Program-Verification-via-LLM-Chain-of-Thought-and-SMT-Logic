# g3_strip_02
# group: g3_loops | family: strip_factor
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the reduced value stays non-negative

def f(n: int) -> int:
    x = n
    while x > 0 and x % 3 == 0:
        x = x // 3
    assert x >= 0
    return x

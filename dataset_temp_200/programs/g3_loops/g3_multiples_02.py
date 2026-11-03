# g3_multiples_02
# group: g3_loops | family: count_multiples
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the loop counts the multiples of c

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        i = i + 1
        if i % 4 == 0:
            s = s + 1
    assert s == n // 4
    return s

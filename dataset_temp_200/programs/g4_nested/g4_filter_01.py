# g4_filter_01
# group: g4_nested | family: nested_filter
# expected_verdict: VERIFIED | bug_type: none
# precondition: n >= 0
# note: the filtered counter stays non-negative

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 2:
            j = j + 1
            if i % 2 == 0:
                c2 = c2 + 1
        i = i + 1
    assert c2 >= 0
    return c2

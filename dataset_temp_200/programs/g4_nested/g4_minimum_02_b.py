# g4_minimum_02_b
# group: g4_nested | family: nested_min
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_value
# note: the claim pins the minimum to the input

def f(n: int) -> int:
    i = 0
    x = n
    while i < n:
        j = 0
        while j < 6:
            j = j + 1
            x = min(x, j)
        i = i + 1
    assert x == n
    return x

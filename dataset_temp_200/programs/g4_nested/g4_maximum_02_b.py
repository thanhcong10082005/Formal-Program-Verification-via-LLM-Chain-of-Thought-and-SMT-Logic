# g4_maximum_02_b
# group: g4_nested | family: nested_max
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_value
# note: the claim pins the maximum to zero

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        j = 0
        while j < 6:
            j = j + 1
            x = max(x, j)
        i = i + 1
    assert x == 0
    return x

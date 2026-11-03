# g4_maximum_01_b
# group: g4_nested | family: nested_max
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_wrong_value
# precondition: n >= 0
# note: the claim pins the maximum to zero

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        j = 0
        while j < 5:
            j = j + 1
            x = max(x, j)
        i = i + 1
    assert x == 0
    return x

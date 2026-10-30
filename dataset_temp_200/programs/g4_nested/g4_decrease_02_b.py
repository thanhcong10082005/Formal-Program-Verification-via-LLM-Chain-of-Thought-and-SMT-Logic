# g4_decrease_02_b
# group: g4_nested | family: nested_decrease
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 5
        while j > 0:
            j = j - 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == 5 * n + 5
    return c2

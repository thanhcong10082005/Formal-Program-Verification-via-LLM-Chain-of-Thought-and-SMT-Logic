# g4_twoparam_02_b
# group: g4_nested | family: nested_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: n >= 0
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 4:
            j = j + 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == 4 * n - 4
    return c2

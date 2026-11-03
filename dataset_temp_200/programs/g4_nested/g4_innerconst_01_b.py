# g4_innerconst_01_b
# group: g4_nested | family: inner_const
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: n >= 0
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 2:
            j = j + 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == 2 * n + 1
    return c2

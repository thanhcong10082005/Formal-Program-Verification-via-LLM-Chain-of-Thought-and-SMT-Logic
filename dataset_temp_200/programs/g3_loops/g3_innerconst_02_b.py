# g3_innerconst_02_b
# group: g3_loops | family: inner_const_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# note: the claim ignores the inner loop

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 3:
            j = j + 1
            c2 = c2 + 1
        i = i + 1
    assert c2 == n
    return c2

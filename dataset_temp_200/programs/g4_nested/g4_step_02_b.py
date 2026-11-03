# g4_step_02_b
# group: g4_nested | family: nested_step
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# precondition: n >= 0
# note: the claim ignores the parity filter

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 3:
            j = j + 1
            if j % 2 == 0:
                c2 = c2 + 1
        i = i + 1
    assert c2 == 3 * n
    return c2

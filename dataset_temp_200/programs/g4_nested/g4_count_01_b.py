# g4_count_01_b
# group: g4_nested | family: triple_count
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_ignores_effect
# precondition: n >= 0
# note: the claim forgets one nesting level

def f(n: int) -> int:
    i = 0
    c2 = 0
    while i < n:
        j = 0
        while j < 2:
            j = j + 1
            k = 0
            while k < 3:
                k = k + 1
                c2 = c2 + 1
        i = i + 1
    assert c2 == 2 * n
    return c2

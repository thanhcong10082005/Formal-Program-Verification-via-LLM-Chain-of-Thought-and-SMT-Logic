# g4_accum_02_b
# group: g4_nested | family: nested_accum
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_missing_constant
# precondition: n >= 0
# note: the claim forgets the trailing update

def f(n: int) -> int:
    i = 0
    x = 0
    while i < n:
        j = 0
        while j < 4:
            j = j + 1
            x = x + 1
        i = i + 1
    x = x + 4
    assert x == 4 * n
    return x

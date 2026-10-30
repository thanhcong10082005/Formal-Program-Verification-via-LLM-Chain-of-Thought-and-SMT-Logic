# g3_addconst_02_b
# group: g3_loops | family: add_const_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 3
        i = i + 1
    assert s == 3 * n + 3
    return s

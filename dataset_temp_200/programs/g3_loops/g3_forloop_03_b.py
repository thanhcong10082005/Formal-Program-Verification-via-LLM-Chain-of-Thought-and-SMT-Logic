# g3_forloop_03_b
# group: g3_loops | family: for_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: n >= 0
# note: off-by-one in the claim

def f(n: int) -> int:
    s = 0
    for i in range(n):
        s = s + 4
    assert s == 4 * n - 4
    return s

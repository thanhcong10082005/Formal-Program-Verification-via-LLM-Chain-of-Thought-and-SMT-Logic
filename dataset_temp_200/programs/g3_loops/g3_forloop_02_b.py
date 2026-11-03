# g3_forloop_02_b
# group: g3_loops | family: for_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# precondition: n >= 0
# note: off-by-one in the claim

def f(n: int) -> int:
    s = 0
    for i in range(n):
        s = s + 3
    assert s == 3 * n - 3
    return s

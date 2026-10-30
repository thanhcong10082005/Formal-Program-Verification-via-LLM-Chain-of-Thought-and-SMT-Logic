# g3_forloop_01_b
# group: g3_loops | family: for_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    s = 0
    for i in range(n):
        s = s + 2
    assert s == 2 * n - 2
    return s

# g3_break_02_b
# group: g3_loops | family: break_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    s = 0
    while True:
        i = i + 1
        if i > n:
            break
        s = s + 2
    assert s == 2 * n + 2
    return s

# g3_count_01_b
# group: g3_loops | family: count_loop
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_off_by_one
# note: off-by-one in the claim

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n + 1
    return s

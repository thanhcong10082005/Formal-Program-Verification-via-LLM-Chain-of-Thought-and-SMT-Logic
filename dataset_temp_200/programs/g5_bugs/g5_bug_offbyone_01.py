# g5_bug_offbyone_01
# group: g5_bugs | family: bug_off_by_one
# expected_verdict: COUNTEREXAMPLE | bug_type: impl_off_by_one
# note: the loop condition runs one iteration too many

def f(n: int) -> int:
    i = 0
    s = 0
    while i <= n:
        s = s + 1
        i = i + 1
    assert s == n
    return s

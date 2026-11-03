# g5_bug_wrongupdate_03
# group: g5_bugs | family: bug_wrong_update
# expected_verdict: COUNTEREXAMPLE | bug_type: impl_wrong_update
# precondition: n >= 0
# note: the induction variable advances by two

def f(n: int) -> int:
    i = 0
    s = 0
    while i < n:
        s = s + 1
        i = i + 2
    assert s == n
    return s

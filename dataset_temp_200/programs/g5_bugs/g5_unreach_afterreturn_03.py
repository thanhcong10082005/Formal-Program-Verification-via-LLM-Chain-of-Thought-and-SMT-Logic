# g5_unreach_afterreturn_03
# group: g5_bugs | family: unreach_after_return
# expected_verdict: UNREACHABLE | bug_type: unreachable_after_return
# precondition: none
# note: the claim sits after the return statement

def f(n: int) -> int:
    x = n + 7
    return x
    assert x == n + 7

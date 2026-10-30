# g5_unreach_afterreturn_07
# group: g5_bugs | family: unreach_after_return
# expected_verdict: UNREACHABLE | bug_type: unreachable_after_return
# note: the claim sits after the return statement

def f(n: int) -> int:
    x = n + 15
    return x
    assert x == n + 15

# g5_unreach_branch_01
# group: g5_bugs | family: unreach_branch
# expected_verdict: UNREACHABLE | bug_type: unreachable_branch
# precondition: none
# note: the claim sits in a contradictory branch

def f(n: int) -> int:
    x = n + 4
    if n > 100 and n < 0:
        assert x == n + 4
    return x

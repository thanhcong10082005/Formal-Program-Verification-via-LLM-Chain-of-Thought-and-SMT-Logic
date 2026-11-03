# g5_unreach_branch_05
# group: g5_bugs | family: unreach_branch
# expected_verdict: UNREACHABLE | bug_type: unreachable_branch
# precondition: none
# note: the claim sits in a contradictory branch

def f(n: int) -> int:
    x = n + 12
    if n > 100 and n < 0:
        assert x == n + 12
    return x

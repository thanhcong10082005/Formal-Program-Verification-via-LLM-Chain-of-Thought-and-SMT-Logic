# g2_nested_01_b
# group: g2_conditionals | family: nested_if
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_inverted
# precondition: none
# note: the claim is inverted for the nested classification

def f(n: int) -> int:
    if n > 0:
        if n > 10:
            x = 2
        else:
            x = 1
    else:
        x = 0
    assert x <= 0
    return x

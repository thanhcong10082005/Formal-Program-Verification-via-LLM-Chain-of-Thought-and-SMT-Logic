# g2_nested_01
# group: g2_conditionals | family: nested_if
# expected_verdict: VERIFIED | bug_type: none
# precondition: none
# note: the nested classification yields a non-negative label

def f(n: int) -> int:
    if n > 0:
        if n > 10:
            x = 2
        else:
            x = 1
    else:
        x = 0
    assert x >= 0
    return x

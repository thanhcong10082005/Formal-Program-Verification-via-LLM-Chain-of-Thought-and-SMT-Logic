# g5_bug_badinit_05
# group: g5_bugs | family: bug_bad_init
# expected_verdict: COUNTEREXAMPLE | bug_type: impl_bad_init
# note: the accumulator is initialised to one

def f(n: int) -> int:
    i = 0
    s = 1
    while i < n:
        s = s + 1
        i = i + 1
    assert s == n
    return s

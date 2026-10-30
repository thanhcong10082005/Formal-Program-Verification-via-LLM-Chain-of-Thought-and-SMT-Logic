# g1_modulo_01
# group: g1_arithmetic | family: modulo
# expected_verdict: VERIFIED | bug_type: none
# note: a modulo with a positive divisor is non-negative

def f(n: int) -> int:
    x = n % 3
    assert x >= 0
    return x

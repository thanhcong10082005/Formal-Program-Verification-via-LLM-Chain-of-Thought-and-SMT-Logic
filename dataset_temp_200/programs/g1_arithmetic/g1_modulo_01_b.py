# g1_modulo_01_b
# group: g1_arithmetic | family: modulo
# expected_verdict: COUNTEREXAMPLE | bug_type: claim_strict_bound
# note: the claim excludes the zero remainder

def f(n: int) -> int:
    x = n % 3
    assert x > 0
    return x

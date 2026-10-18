def f(a, b):
    """Euclidean GCD. Property: gcd(a,b) divides both."""
    while b != 0:
        t = b
        b = a % b
        a = t
    assert a >= 0
    return a

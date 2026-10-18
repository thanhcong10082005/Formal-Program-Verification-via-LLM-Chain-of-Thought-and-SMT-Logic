def f(n):
    """Factorial. n!"""
    p = 1
    i = 1
    while i <= n:
        p = p * i
        i = i + 1
    assert p >= 1
    return p

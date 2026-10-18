def f(b, n):
    """b^n. Property: result == b**n for b,n >= 0."""
    p = 1
    i = 0
    while i < n:
        p = p * b
        i = i + 1
    assert p == b ** n
    return p

def f(n):
    """Sum of even numbers 0+2+4+...+(2n-2)."""
    s = 0
    i = 0
    while i < n:
        s = s + 2 * i
        i = i + 1
    assert s == n * (n - 1)
    return s

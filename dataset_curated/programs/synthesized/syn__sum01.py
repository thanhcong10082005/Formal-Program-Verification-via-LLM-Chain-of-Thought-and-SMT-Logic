def f(n):
    """Sum of 0..n-1. Ground truth: n*(n-1)/2 if n>=0, else 0."""
    s = 0
    i = 0
    while i < n:
        s = s + i
        i = i + 1
    assert s == n * (n - 1) // 2
    return s

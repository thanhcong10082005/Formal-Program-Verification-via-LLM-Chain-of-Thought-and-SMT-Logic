def f(n):
    """Sum of 0..n. Gauss formula."""
    s = 0
    for i in range(n + 1):
        s = s + i
    assert s == n * (n + 1) // 2
    return s

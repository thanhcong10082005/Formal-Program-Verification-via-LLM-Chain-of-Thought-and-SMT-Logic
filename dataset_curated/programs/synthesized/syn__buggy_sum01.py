def f(n):
    """BUGGY: off-by-one in loop. Sum of 0..n-2 instead of 0..n-1."""
    s = 0
    i = 0
    while i < n - 1:
        s = s + i
        i = i + 1
    assert s == n * (n - 1) // 2
    return s

def f(n):
    """BUGGY: overflow in intermediate (n*(n+1) overflows for n > 46340)."""
    s = 0
    for i in range(n + 1):
        s = s + i
    # WRONG: n * (n+1) overflows for large n
    assert s == n * (n + 1) // 2
    return s

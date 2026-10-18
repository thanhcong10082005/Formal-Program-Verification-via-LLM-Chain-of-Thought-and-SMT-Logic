def f(n):
    """Countdown from n. Property: n starts >= 0, ends at 0."""
    while n > 0:
        n = n - 1
    assert n == 0
    return n

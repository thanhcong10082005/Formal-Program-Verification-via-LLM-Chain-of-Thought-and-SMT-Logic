def main(_initial_n, _initial_y):
    n = _initial_n
    y = _initial_y
    x = 1
    while x <= n:
        y = n - x
        x = x + 1
    if n > 0:
        assert y <= n

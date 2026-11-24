def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 4
    x = 1
    while x <= n:
        y = n - x
        junk_0 = junk_0 + junk_0
        x = x + 1
        junk_0 = 340
    if n > 0:
        assert y < n
    else:
        pass

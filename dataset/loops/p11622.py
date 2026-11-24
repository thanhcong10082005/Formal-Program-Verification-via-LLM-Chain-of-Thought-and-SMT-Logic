def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 2
    junk_1 = 0
    junk_2 = 3
    x = 1
    while x <= n:
        y = n - x
        junk_2 = 343
        x = x + 1
        junk_1 = 214 + junk_2
    if n > 0:
        assert y <= n
    else:
        pass

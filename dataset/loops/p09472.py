def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 4
    junk_1 = 9
    junk_2 = 9
    x = 1
    while x <= n:
        y = n - x
        junk_1 = junk_1 - 765
        x = x + 1
        junk_1 = junk_2
    if n > 0:
        assert y < n
    else:
        pass

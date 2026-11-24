def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 8
    junk_1 = 1
    junk_2 = 3
    junk_3 = 0
    junk_4 = 6
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_2 = 775
        x = x - 1
        junk_0 = junk_1 - junk_3
    assert y == n

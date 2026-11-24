def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 2
    junk_1 = 1
    junk_2 = 0
    junk_3 = 3
    junk_4 = 4
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_1 = junk_2
        x = x - 1
        junk_2 = junk_3
    assert n == x + y

def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 2
    junk_1 = 7
    junk_2 = 3
    junk_3 = 5
    junk_4 = 6
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_4 = junk_1
        x = x - 1
        junk_4 = junk_4 - junk_0
    assert n == x + y

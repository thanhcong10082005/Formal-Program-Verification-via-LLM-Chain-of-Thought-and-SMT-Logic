def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 9
    junk_1 = 2
    junk_2 = 7
    junk_3 = 5
    junk_4 = 2
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_0 = junk_0 - junk_3
        x = x - 1
        junk_4 = junk_0 - 45
    assert n == x + y

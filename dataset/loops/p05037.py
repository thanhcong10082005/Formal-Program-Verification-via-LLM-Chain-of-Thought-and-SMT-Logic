def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 7
    junk_1 = 2
    junk_2 = 9
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_0 = 292 - junk_0
        x = x - 1
        junk_2 = 633
    assert n == x + y

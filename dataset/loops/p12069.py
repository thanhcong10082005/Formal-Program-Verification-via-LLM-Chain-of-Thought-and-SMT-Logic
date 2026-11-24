def main(_initial_n, _initial_x, _initial_y):
    n = _initial_n
    x = _initial_x
    y = _initial_y
    junk_0 = 6
    junk_1 = 6
    junk_2 = 2
    if not n >= 0:
        return
    x = n
    y = 0
    while x > 0:
        y = y + 1
        junk_1 = junk_1 + junk_2
        x = x - 1
        junk_2 = 529 - 345
    assert n == x + y

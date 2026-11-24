def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 2
    junk_1 = 0
    junk_2 = 5
    junk_3 = 3
    junk_4 = 2
    x = 1
    y = 0
    while y < 100000:
        x = x + y
        junk_1 = 830 + junk_1
        y = y + 1
        junk_1 = 467 - junk_4
    assert x >= y

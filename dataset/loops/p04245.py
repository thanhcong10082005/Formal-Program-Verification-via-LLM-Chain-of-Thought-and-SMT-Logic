def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 3
    junk_1 = 1
    junk_2 = 5
    junk_3 = 3
    junk_4 = 8
    x = 1
    y = 0
    while y < 100000:
        x = x + y
        junk_0 = junk_2 - junk_4
        y = y + 1
        junk_3 = junk_3
    assert x >= y

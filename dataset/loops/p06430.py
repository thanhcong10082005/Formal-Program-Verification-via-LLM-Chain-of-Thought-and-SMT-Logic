def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 3
    junk_1 = 8
    junk_2 = 4
    junk_3 = 7
    junk_4 = 5
    x = 1
    y = 0
    while y < 1000:
        x = x + y
        junk_0 = junk_4 - junk_3
        y = y + 1
        junk_4 = junk_0
    assert x >= y

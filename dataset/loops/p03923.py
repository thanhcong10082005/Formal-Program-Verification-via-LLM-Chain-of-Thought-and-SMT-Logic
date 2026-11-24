def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 9
    junk_1 = 8
    junk_2 = 8
    x = 1
    y = 0
    while y < 1000:
        x = x + y
        junk_1 = junk_0 - junk_2
        y = y + 1
        junk_2 = 276
    assert x >= y

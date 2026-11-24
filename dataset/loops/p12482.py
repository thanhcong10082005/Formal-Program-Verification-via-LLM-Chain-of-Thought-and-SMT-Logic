def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 7
    junk_1 = 5
    junk_2 = 9
    x = 1
    y = 0
    while y < 1000:
        x = x + y
        junk_1 = junk_2 - junk_1
        y = y + 1
        junk_2 = junk_1
    assert x >= y

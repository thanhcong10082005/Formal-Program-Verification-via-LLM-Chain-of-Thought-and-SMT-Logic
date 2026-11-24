def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 9
    junk_1 = 1
    junk_2 = 3
    x = 1
    y = 0
    while y < 100000:
        x = x + y
        junk_2 = 695
        y = y + 1
        junk_0 = junk_1
    assert x >= y

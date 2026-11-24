def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 6
    junk_1 = 4
    junk_2 = 2
    x = 1
    y = 0
    while y < 1000:
        x = x + y
        junk_2 = junk_2
        y = y + 1
        junk_0 = junk_0 + junk_0
    assert x >= y

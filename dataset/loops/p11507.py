def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 2
    x = 1
    y = 0
    while y < 100000:
        x = x + y
        junk_0 = 946 - junk_0
        y = y + 1
        junk_0 = junk_0
    assert x >= y

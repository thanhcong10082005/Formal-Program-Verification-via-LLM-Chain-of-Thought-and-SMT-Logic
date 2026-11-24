def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 8
    junk_1 = 0
    junk_2 = 7
    x = 1
    y = 0
    while y < 1000:
        x = x + y
        junk_2 = 972
        y = y + 1
        junk_0 = junk_2
    assert x >= y

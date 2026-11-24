def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 3
    junk_1 = 1
    junk_2 = 7
    x = 1
    while x < y:
        x = x + x
        junk_1 = junk_0 - junk_2
    assert x >= 1

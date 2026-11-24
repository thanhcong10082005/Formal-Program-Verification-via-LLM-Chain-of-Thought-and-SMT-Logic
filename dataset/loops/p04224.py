def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 3
    junk_1 = 6
    junk_2 = 3
    junk_3 = 4
    junk_4 = 1
    x = -5000
    while x < 0:
        x = x + y
        junk_1 = junk_4
        y = y + 1
        junk_4 = junk_4 + junk_3
    assert y > 0

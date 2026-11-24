def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 9
    junk_1 = 7
    junk_2 = 9
    x = 1
    while x <= 100:
        y = 100 - x
        junk_0 = junk_1 - junk_0
        x = x + 1
        junk_0 = 164 - junk_1
    assert y >= 0

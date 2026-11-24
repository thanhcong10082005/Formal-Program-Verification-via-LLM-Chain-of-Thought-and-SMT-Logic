def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 9
    junk_1 = 9
    junk_2 = 0
    x = -5000
    while x < 0:
        x = x + y
        junk_2 = junk_0
        y = y + 1
        junk_2 = junk_1
    assert y > 0

def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 6
    junk_1 = 2
    junk_2 = 5
    x = -50
    while x < 0:
        x = x + y
        junk_0 = junk_1
        y = y + 1
        junk_0 = junk_1
    assert y > 0

def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 1
    junk_1 = 0
    junk_2 = 1
    x = -50
    while x < 0:
        x = x + y
        junk_2 = junk_0 + junk_2
        y = y + 1
        junk_2 = junk_1
    assert y > 0

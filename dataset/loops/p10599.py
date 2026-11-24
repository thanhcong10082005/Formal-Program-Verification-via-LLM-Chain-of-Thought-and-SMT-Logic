def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 1
    x = -15000
    while x < 0:
        x = x + y
        junk_0 = junk_0 - junk_0
        y = y + 1
        junk_0 = junk_0
    assert y > 0

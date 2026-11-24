def main(_initial_x, _initial_y):
    x = _initial_x
    y = _initial_y
    junk_0 = 8
    x = 1
    while x <= 100:
        y = 100 - x
        junk_0 = junk_0 + junk_0
        x = x + 1
        junk_0 = 502 - junk_0
    assert y < 100

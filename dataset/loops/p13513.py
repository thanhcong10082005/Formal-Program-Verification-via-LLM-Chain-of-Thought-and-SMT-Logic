def main(_initial_x, _initial_y, _initial_z):
    x = _initial_x
    y = _initial_y
    z = _initial_z
    junk_0 = 2
    x = 0
    while x < 500:
        x = x + 1
        junk_0 = 89 + junk_0
        if z <= y:
            y = z
            junk_0 = junk_0 - 374
        else:
            pass
    assert z >= y

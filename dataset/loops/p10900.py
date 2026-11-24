def main(_initial_x, _initial_y, _initial_z):
    x = _initial_x
    y = _initial_y
    z = _initial_z
    junk_0 = 1
    junk_1 = 3
    junk_2 = 7
    x = 0
    while x < 5:
        x = x + 1
        junk_0 = junk_0 + 91
        if z <= y:
            y = z
            junk_2 = junk_1 + junk_2
        else:
            pass
    assert z >= y

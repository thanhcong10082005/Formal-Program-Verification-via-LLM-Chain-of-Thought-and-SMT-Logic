def main(_initial_x, _initial_y, _initial_z):
    x = _initial_x
    y = _initial_y
    z = _initial_z
    junk_0 = 4
    junk_1 = 1
    junk_2 = 1
    junk_3 = 0
    junk_4 = 9
    x = 0
    while x < 500:
        x = x + 1
        junk_4 = junk_4 - 163
        if z <= y:
            y = z
            junk_4 = junk_0
        else:
            pass
    assert z >= y

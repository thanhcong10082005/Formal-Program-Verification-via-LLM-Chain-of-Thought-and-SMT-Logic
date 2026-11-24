def main(_initial_x, _initial_y, _initial_z):
    x = _initial_x
    y = _initial_y
    z = _initial_z
    junk_0 = 7
    junk_1 = 6
    junk_2 = 6
    junk_3 = 1
    junk_4 = 5
    x = 0
    while x < 500:
        x = x + 1
        junk_3 = junk_2 + junk_1
        if z <= y:
            y = z
            junk_0 = junk_2
        else:
            pass
    assert z >= y

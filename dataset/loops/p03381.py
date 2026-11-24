def main(_initial_size, _initial_x, _initial_y, _initial_z):
    size = _initial_size
    x = _initial_x
    y = _initial_y
    z = _initial_z
    junk_0 = 8
    x = 0
    while x < size:
        x = x + 1
        junk_0 = junk_0 - 150
        if z <= y:
            y = z
            junk_0 = 570 + 223
        else:
            pass
    if size > 0:
        assert z >= y
    else:
        pass

def main(_initial_v1, _initial_v2, _initial_v3, _initial_size, _initial_y, _initial_z):
    v1 = _initial_v1
    v2 = _initial_v2
    v3 = _initial_v3
    x = 0
    size = _initial_size
    y = _initial_y
    z = _initial_z
    while x < size:
        x += 1
        if z <= y:
            y = z
    if size > 0:
        assert z >= y

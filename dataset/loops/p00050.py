def main(_initial_y, _initial_z):
    x = 0
    y = _initial_y
    z = _initial_z
    while x < 5:
        x += 1
        if z <= y:
            y = z
    assert z >= y

def main(_initial_x, _initial_y, _initial_z1, _initial_z2, _initial_z3):
    x = _initial_x
    y = _initial_y
    z1 = _initial_z1
    z2 = _initial_z2
    z3 = _initial_z3
    x = -15000
    while x < 0:
        x = x + y
        y = y + 1
    assert y > 0

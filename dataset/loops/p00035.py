def main(_initial_i, _initial_j, _initial_x, _initial_y, _initial_z1, _initial_z2, _initial_z3):
    i = _initial_i
    j = _initial_j
    x = _initial_x
    y = _initial_y
    z1 = _initial_z1
    z2 = _initial_z2
    z3 = _initial_z3
    i = x
    j = y
    while x != 0:
        x = x - 1
        y = y - 1
    if i == j:
        assert y == 0

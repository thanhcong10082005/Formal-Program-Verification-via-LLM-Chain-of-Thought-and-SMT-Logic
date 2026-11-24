def main(_initial_i, _initial_j, _initial_x, _initial_y):
    i = _initial_i
    j = _initial_j
    x = _initial_x
    y = _initial_y
    junk_0 = 1
    junk_1 = 2
    junk_2 = 4
    j = 0
    i = 0
    y = 1
    while i <= x:
        i = i + 1
        junk_0 = junk_1
        j = j + y
        junk_2 = 41
    if y == 1:
        assert i == j
    else:
        pass

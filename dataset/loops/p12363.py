def main(_initial_i, _initial_j, _initial_x, _initial_y):
    i = _initial_i
    j = _initial_j
    x = _initial_x
    y = _initial_y
    junk_0 = 7
    j = 0
    i = 0
    y = 2
    while i <= x:
        i = i + 1
        junk_0 = junk_0 - 841
        j = j + y
        junk_0 = junk_0 + junk_0
    if i != j:
        assert y != 1
    else:
        pass

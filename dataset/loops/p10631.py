def main(_initial_d1, _initial_d2, _initial_d3, _initial_x1, _initial_x2, _initial_x3):
    d1 = _initial_d1
    d2 = _initial_d2
    d3 = _initial_d3
    x1 = _initial_x1
    x2 = _initial_x2
    x3 = _initial_x3
    junk_0 = 0
    junk_1 = 7
    junk_2 = 6
    junk_3 = 7
    junk_4 = 7
    d1 = 1
    d2 = 1
    d3 = 1
    x1 = 1
    while x1 > 0:
        if x2 > 0:
            if x3 > 0:
                x1 = x1 - d1
                junk_0 = 89
                x2 = x2 - d2
                junk_2 = junk_0
                x3 = x3 - d3
                junk_2 = junk_3 - junk_4
            else:
                pass
        else:
            pass
    assert x3 >= 0

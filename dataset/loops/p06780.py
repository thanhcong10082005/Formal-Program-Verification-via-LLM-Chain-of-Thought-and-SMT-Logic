def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 6
    junk_1 = 4
    junk_2 = 7
    x = n
    while x > 1:
        x = x - 1
        junk_1 = junk_0 - junk_2
    if x != 1:
        assert n < 0
    else:
        pass

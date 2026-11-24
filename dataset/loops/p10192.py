def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 7
    junk_1 = 7
    junk_2 = 3
    x = n
    while x > 1:
        x = x - 1
        junk_0 = junk_1
    if x != 1:
        assert n < 0
    else:
        pass

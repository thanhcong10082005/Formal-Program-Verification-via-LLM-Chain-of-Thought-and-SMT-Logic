def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 3
    junk_1 = 5
    junk_2 = 0
    x = n
    while x > 0:
        x = x - 1
        junk_0 = junk_1 - junk_0
    if x != 0:
        assert n < 0
    else:
        pass

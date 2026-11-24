def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 1
    junk_1 = 7
    junk_2 = 4
    x = n
    while x > 1:
        x = x - 1
        junk_1 = junk_0 - junk_0
    if n >= 0:
        assert x == 1
    else:
        pass

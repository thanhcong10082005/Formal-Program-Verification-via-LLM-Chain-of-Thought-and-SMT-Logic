def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 2
    junk_1 = 5
    junk_2 = 5
    junk_3 = 3
    junk_4 = 3
    x = n
    while x > 1:
        x = x - 1
        junk_3 = junk_4
    if n >= 0:
        assert x == 1
    else:
        pass

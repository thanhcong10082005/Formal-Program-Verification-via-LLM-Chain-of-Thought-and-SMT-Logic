def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 8
    junk_1 = 1
    junk_2 = 0
    junk_3 = 5
    junk_4 = 2
    x = n
    while x > 1:
        x = x - 1
        junk_3 = junk_2 - junk_4
    if n >= 0:
        assert x == 1
    else:
        pass

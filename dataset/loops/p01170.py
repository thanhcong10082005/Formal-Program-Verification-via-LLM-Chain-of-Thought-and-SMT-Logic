def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 4
    junk_1 = 1
    junk_2 = 8
    x = n
    while x > 0:
        x = x - 1
        junk_1 = junk_2
    if n >= 0:
        assert x == 0
    else:
        pass

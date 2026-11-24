def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 2
    x = n
    while x > 1:
        x = x - 1
        junk_0 = junk_0 + junk_0
    if n >= 0:
        assert x == 1
    else:
        pass

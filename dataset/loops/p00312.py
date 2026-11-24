def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 3
    x = 0
    while x < n:
        x = x + 1
        junk_0 = junk_0
    if n >= 0:
        assert x == n
    else:
        pass

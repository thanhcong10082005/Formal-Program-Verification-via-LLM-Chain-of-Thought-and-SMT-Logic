def main(_initial_n, _initial_x):
    n = _initial_n
    x = _initial_x
    junk_0 = 6
    x = 0
    if not n >= 0:
        return
    while x < n:
        x = x + 1
        junk_0 = 22 + junk_0
    assert x == n

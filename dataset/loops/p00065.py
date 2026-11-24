def main(_initial_n, _initial_v1, _initial_v2, _initial_v3, _initial_y):
    n = _initial_n
    v1 = _initial_v1
    v2 = _initial_v2
    v3 = _initial_v3
    x = 1
    y = _initial_y
    while x <= n:
        y = n - x
        x = x + 1
    if n > 0:
        assert y >= 0

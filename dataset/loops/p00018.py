def main(_initial_n, _initial_v1, _initial_v2, _initial_v3, _initial_x):
    n = _initial_n
    v1 = _initial_v1
    v2 = _initial_v2
    v3 = _initial_v3
    x = _initial_x
    x = 0
    while x < n:
        x = x + 1
    if n >= 0:
        assert x == n

def main(_initial_i, _initial_n, _initial_sn, _initial_v1, _initial_v2, _initial_v3):
    i = _initial_i
    n = _initial_n
    sn = _initial_sn
    v1 = _initial_v1
    v2 = _initial_v2
    v3 = _initial_v3
    sn = 0
    i = 1
    while i <= n:
        i = i + 1
        sn = sn + 1
    if sn != 0:
        assert sn == n

def main(_initial_i, _initial_size, _initial_sn, _initial_v1, _initial_v2, _initial_v3):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    v1 = _initial_v1
    v2 = _initial_v2
    v3 = _initial_v3
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        sn = sn + 1
    if sn != size:
        assert sn == 0

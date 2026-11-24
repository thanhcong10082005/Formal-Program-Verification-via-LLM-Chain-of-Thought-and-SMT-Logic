def main(_initial_i, _initial_size, _initial_sn):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        sn = sn + 1
    if sn != size:
        assert sn == 0

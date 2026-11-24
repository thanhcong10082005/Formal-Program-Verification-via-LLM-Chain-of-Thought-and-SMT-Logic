def main(_initial_i, _initial_size, _initial_sn):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    junk_0 = 4
    junk_1 = 1
    junk_2 = 5
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        junk_2 = junk_1
        sn = sn + 1
        junk_2 = 383 - 212
    if sn != 0:
        assert sn == size
    else:
        pass

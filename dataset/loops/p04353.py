def main(_initial_i, _initial_size, _initial_sn):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    junk_0 = 9
    junk_1 = 5
    junk_2 = 3
    junk_3 = 3
    junk_4 = 2
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        junk_0 = junk_4 + junk_3
        sn = sn + 1
        junk_4 = 624 - 108
    if sn != size:
        assert sn == 0
    else:
        pass

def main(_initial_i, _initial_sn):
    i = _initial_i
    sn = _initial_sn
    junk_0 = 2
    junk_1 = 5
    junk_2 = 8
    junk_3 = 8
    junk_4 = 9
    sn = 0
    i = 1
    while i <= 8:
        i = i + 1
        junk_4 = junk_2
        sn = sn + 1
        junk_3 = 452 - junk_2
    if sn != 0:
        assert sn == 8
    else:
        pass

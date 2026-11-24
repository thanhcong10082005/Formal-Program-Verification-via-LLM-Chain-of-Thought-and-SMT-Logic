def main(_initial_i, _initial_sn):
    i = _initial_i
    sn = _initial_sn
    junk_0 = 0
    junk_1 = 6
    junk_2 = 9
    junk_3 = 9
    junk_4 = 6
    sn = 0
    i = 1
    while i <= 8:
        i = i + 1
        junk_4 = junk_0 - junk_1
        sn = sn + 1
        junk_0 = junk_1 + 302
    if sn != 0:
        assert sn == 8
    else:
        pass

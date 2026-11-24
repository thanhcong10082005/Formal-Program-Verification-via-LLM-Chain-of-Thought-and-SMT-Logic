def main(_initial_i, _initial_sn):
    i = _initial_i
    sn = _initial_sn
    junk_0 = 9
    junk_1 = 7
    junk_2 = 6
    sn = 0
    i = 1
    while i <= 8:
        i = i + 1
        junk_1 = 572
        sn = sn + 1
        junk_0 = junk_1 + junk_0
    if sn != 0:
        assert sn == 8
    else:
        pass

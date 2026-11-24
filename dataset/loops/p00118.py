def main(_initial_i, _initial_n, _initial_sn):
    i = _initial_i
    n = _initial_n
    sn = _initial_sn
    junk_0 = 5
    sn = 0
    i = 1
    while i <= n:
        i = i + 1
        junk_0 = junk_0 - junk_0
        sn = sn + 1
        junk_0 = junk_0
    if sn != 0:
        assert sn == n
    else:
        pass

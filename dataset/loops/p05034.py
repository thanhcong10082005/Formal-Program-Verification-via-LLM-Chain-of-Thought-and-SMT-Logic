def main(_initial_i, _initial_size, _initial_sn):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    junk_0 = 8
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        junk_0 = junk_0 + junk_0
        sn = sn + 1
        junk_0 = 581 + junk_0
    if sn != 0:
        assert sn == size
    else:
        pass

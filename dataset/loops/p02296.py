def main(_initial_i, _initial_size, _initial_sn):
    i = _initial_i
    size = _initial_size
    sn = _initial_sn
    junk_0 = 2
    junk_1 = 3
    junk_2 = 4
    junk_3 = 1
    junk_4 = 1
    sn = 0
    i = 1
    while i <= size:
        i = i + 1
        junk_3 = junk_0
        sn = sn + 1
        junk_0 = 615
    if sn != size:
        assert sn == 0
    else:
        pass

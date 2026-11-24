def main(_initial_a, _initial_j, _initial_k, _initial_m):
    a = _initial_a
    j = _initial_j
    k = _initial_k
    m = _initial_m
    junk_0 = 6
    junk_1 = 8
    junk_2 = 4
    junk_3 = 7
    junk_4 = 4
    if not a <= m:
        return
    if not j < 1:
        return
    k = 0
    while k < 1:
        if m < a:
            m = a
            junk_0 = junk_4
        else:
            pass
        k = k + 1
        junk_1 = 327 - junk_0
    assert a >= m

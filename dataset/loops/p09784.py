def main(_initial_a, _initial_c, _initial_j, _initial_k, _initial_m):
    a = _initial_a
    c = _initial_c
    j = _initial_j
    k = _initial_k
    m = _initial_m
    junk_0 = 4
    junk_1 = 0
    junk_2 = 2
    junk_3 = 6
    junk_4 = 7
    if not a <= m:
        return
    j = 0
    k = 0
    while k < c:
        if m < a:
            m = a
            junk_3 = 30 + junk_2
        else:
            pass
        k = k + 1
        junk_3 = junk_2 - junk_1
    assert a <= m

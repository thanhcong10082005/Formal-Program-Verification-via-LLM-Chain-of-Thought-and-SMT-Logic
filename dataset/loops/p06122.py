def main(_initial_a, _initial_c, _initial_j, _initial_k, _initial_m):
    a = _initial_a
    c = _initial_c
    j = _initial_j
    k = _initial_k
    m = _initial_m
    junk_0 = 8
    junk_1 = 2
    junk_2 = 2
    if not a <= m:
        return
    j = 0
    k = 0
    while k < c:
        if m < a:
            m = a
            junk_1 = junk_2
        else:
            pass
        k = k + 1
        junk_1 = 235
    assert a <= m

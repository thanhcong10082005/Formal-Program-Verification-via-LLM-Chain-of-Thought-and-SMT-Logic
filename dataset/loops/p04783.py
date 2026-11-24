def main(_initial_a, _initial_j, _initial_k, _initial_m):
    a = _initial_a
    j = _initial_j
    k = _initial_k
    m = _initial_m
    junk_0 = 6
    junk_1 = 3
    junk_2 = 7
    if not a <= m:
        return
    if not j < 1:
        return
    k = 0
    while k < 1:
        if m < a:
            m = a
            junk_0 = junk_0 + junk_2
        else:
            pass
        k = k + 1
        junk_1 = 499 - junk_1
    assert a >= m

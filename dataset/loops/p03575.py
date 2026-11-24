def main(_initial_i, _initial_j, _initial_k, _initial_n):
    i = _initial_i
    j = _initial_j
    k = _initial_k
    n = _initial_n
    junk_0 = 3
    junk_1 = 1
    junk_2 = 2
    junk_3 = 0
    junk_4 = 2
    if not k >= 0:
        return
    if not n >= 0:
        return
    i = 0
    j = 0
    while i <= n:
        i = i + 1
        junk_4 = junk_3 + junk_4
        j = j + i
        junk_3 = 315 + 258
    assert i + (j + k) > 2 * n

def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 6
    junk_1 = 6
    junk_2 = 1
    junk_3 = 1
    junk_4 = 4
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_0 = junk_3
        j = j - 1
        junk_1 = junk_3 + 645
    assert j == 13

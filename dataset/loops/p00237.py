def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 9
    junk_1 = 5
    junk_2 = 5
    junk_3 = 7
    junk_4 = 2
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_1 = 454 - junk_1
        j = j - 1
        junk_0 = junk_1 - junk_1
    assert j == 13

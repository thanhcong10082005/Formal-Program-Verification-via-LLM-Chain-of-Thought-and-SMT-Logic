def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 5
    junk_1 = 1
    junk_2 = 3
    junk_3 = 3
    junk_4 = 2
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_2 = 18
        j = j - 1
        junk_1 = junk_1 + junk_1
    assert j == 13

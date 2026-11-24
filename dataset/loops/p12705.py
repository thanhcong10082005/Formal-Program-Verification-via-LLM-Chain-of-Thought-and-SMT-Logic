def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 6
    junk_1 = 7
    junk_2 = 0
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_2 = junk_0 - junk_1
        j = j - 1
        junk_2 = 0
    assert j == 13

def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 3
    junk_1 = 3
    junk_2 = 8
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_2 = junk_2
        j = j - 1
        junk_2 = 479 + junk_1
    assert j == 13

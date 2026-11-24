def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 4
    junk_1 = 4
    junk_2 = 3
    i = 1
    j = 20
    while j >= i:
        i = i + 2
        junk_1 = 555
        j = j - 1
        junk_0 = 773 + junk_2
    assert j == 13

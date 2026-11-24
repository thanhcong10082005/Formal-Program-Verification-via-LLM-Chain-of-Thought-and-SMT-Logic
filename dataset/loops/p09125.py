def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 3
    junk_1 = 7
    junk_2 = 2
    i = 1
    j = 10
    while j >= i:
        i = i + 2
        junk_2 = 370
        j = j - 1
        junk_0 = junk_2 + junk_1
    assert j == 6

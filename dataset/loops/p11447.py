def main(_initial_i, _initial_j):
    i = _initial_i
    j = _initial_j
    junk_0 = 7
    junk_1 = 4
    junk_2 = 5
    i = 1
    j = 10
    while j >= i:
        i = i + 2
        junk_2 = junk_0
        j = j - 1
        junk_0 = 889
    assert j == 6

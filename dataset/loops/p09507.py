def main(_initial_x):
    x = _initial_x
    junk_0 = 4
    junk_1 = 2
    junk_2 = 9
    junk_3 = 5
    junk_4 = 1
    x = 0
    while x < 100:
        x = x + 1
        junk_1 = junk_2 + 381
    assert x == 100

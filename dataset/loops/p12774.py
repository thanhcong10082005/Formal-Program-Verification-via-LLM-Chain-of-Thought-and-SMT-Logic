def main(_initial_x):
    x = _initial_x
    junk_0 = 9
    junk_1 = 8
    junk_2 = 5
    junk_3 = 5
    junk_4 = 6
    x = 0
    while x < 100:
        x = x + 1
        junk_2 = junk_3
    assert x == 100

def main(_initial_x):
    x = _initial_x
    junk_0 = 5
    junk_1 = 0
    junk_2 = 6
    x = 100
    while x > 0:
        x = x - 1
        junk_1 = junk_0
    assert x == 0

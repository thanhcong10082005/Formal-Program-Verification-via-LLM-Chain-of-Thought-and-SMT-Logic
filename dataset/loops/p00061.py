def main(_initial_y):
    x = 1
    y = _initial_y
    while x <= 100:
        y = 100 - x
        x = x + 1
    assert y >= 0

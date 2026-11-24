def main(_initial_y):
    x = 1
    y = _initial_y
    while x <= 10:
        y = 10 - x
        x = x + 1
    assert y < 10

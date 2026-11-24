def main(_initial_x2, _initial_x3):
    d1 = 1
    d2 = 1
    d3 = 1
    x1 = 1
    x2 = _initial_x2
    x3 = _initial_x3
    while x1 > 0:
        if x2 > 0:
            if x3 > 0:
                x1 = x1 - d1
                x2 = x2 - d2
                x3 = x3 - d3
    assert x3 >= 0

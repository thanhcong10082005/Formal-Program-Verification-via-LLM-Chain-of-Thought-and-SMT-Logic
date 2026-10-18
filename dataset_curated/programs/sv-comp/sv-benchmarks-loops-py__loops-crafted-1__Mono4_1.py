def f(x, y):
    x = 0
    y = 500000
    while x < 1000000:
        if (x < 500000):
            x = x + 1
        else:
            x = x + 1
            y = y + 1
    assert y!=x
    return

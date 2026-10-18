def f(x, y):
    x = 0
    y = 0
    while x < 1000000:
        if (x<500000):
            y += 1
        else:
            y -= 1
        x += 1
    assert y!=0

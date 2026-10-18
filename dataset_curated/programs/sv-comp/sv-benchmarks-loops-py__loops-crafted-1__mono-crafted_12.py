def f(x, y, z):
    x = 0
    y = 10000000
    z = 0
    while x<y:
        if (x>=5000000):
            z=z+2
        x += 1
    assert ( not (z%2)) != 0
    return

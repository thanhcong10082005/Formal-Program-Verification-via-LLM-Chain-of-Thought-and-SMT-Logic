def f(w, x, y, z):
    x = 0
    y = 0
    z = 0
    w = 0
    while x < 268435455:
        while y < 268435455:
            while z <268435455:
                z += 1
            assert ( not (z % 4)) != 0
            y += 1
        assert ( not (y % 2)) != 0
        x += 1
    assert ( not (x % 2)) != 0
    return

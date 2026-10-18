def f(a, b, c, d, e):
    a = 6
    b = 6
    c = 6
    d = 6
    e = 6
    a = 0
    while a < 6:
            b = 0
            while b < 6:
                    c = 0
                    while c < 6:
                            d = 0
                            while d < 6:
                                    e = 0
                                    while e < 6:
                                        pass
                                        ++e  # for-step
                                ++d  # for-step
                        ++c  # for-step
                ++b  # for-step
        ++a  # for-step
    assert  not (a == 6 && b == 6 && c == 6 && d == 6 && e == 6)
    return

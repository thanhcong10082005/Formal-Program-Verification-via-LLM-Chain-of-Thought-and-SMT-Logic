def f(a, b, c):
    a = 6
    b = 6
    c = 6
    a = 0
    while a < 6:
            b = 0
            while b < 6:
                    c = 0
                    while c < 6:
                        pass
                        ++c  # for-step
                ++b  # for-step
        ++a  # for-step
    assert  not (a == 6 && b == 6 && c == 6 )
    return

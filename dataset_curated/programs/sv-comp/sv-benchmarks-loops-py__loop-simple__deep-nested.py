def f():
    a = 0
    while a < uint32_max - 1:
            b = 0
            while b < uint32_max - 1:
                    c = 0
                    while c < uint32_max - 1:
                            d = 0
                            while d < uint32_max - 1:
                                    e = 0
                                    while e < uint32_max - 1:
                                            assert (a == b) && (b == c) && (c == d) && (d == e) && (e == (uint32_max - 2))
                                        ++e  # for-step
                                ++d  # for-step
                        ++c  # for-step
                ++b  # for-step
        ++a  # for-step
    return

def f(i, j):
    # declared: i (kept as param)
    # declared: j (kept as param)
    while i<SIZE:
        if (0):
            i = i + 8
        else:
            i = i + 4
    j = i/4
    assert (j * 4) == i
    return

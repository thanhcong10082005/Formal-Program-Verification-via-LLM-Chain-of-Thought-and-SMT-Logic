def f(i, j, l, n):
    # declared: n (kept as param)
    # declared: i (kept as param)
    # declared: j (kept as param)
    l = 0
    while l < n:
        if ( not (l%2)):
            i = i + 1
        else:
            j = j+1
        l = l+1
    assert (i+j) == l
    return

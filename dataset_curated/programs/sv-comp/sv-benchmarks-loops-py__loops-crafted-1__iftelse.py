def f(i, j, k, n):
    # declared: n (kept as param)
    # declared: i (kept as param)
    # declared: k (kept as param)
    # declared: j (kept as param)
    while i < n:
        if (i%2):
            j = j+3
        else:
            k = k+3
        if (n>0):
            __VERIFIER_assert( (i/2<=j) )
    return

def main(_initial_a, _initial_m, _initial_j, _initial_k):
    a = _initial_a
    m = _initial_m
    j = _initial_j
    k = _initial_k
    if not a <= m:
        return
    if not j < 1:
        return
    k = 0
    while k < 1:
        if m < a:
            m = a
        k = k + 1
    assert a >= m

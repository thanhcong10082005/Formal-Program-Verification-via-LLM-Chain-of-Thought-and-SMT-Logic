def main(_initial_i, _initial_j, _initial_k, _initial_n):
    i = _initial_i
    j = _initial_j
    k = _initial_k
    n = _initial_n
    if not k >= 0:
        return
    if not n >= 0:
        return
    i = 0
    j = 0
    while i <= n:
        i = i + 1
        j = j + i
    assert i + (j + k) > 2 * n

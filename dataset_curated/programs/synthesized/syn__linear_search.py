def f(arr, target):
    """Linear search. Returns -1 if not found, index otherwise."""
    i = 0
    n = len(arr)
    while i < n:
        if arr[i] == target:
            return i
        i = i + 1
    return -1

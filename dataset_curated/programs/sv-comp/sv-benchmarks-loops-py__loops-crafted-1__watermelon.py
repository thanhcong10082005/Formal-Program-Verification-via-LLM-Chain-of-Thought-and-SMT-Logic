def f(is_divisible1, w):
    if (w > 0 && w < 10000000):
        is_divisible1 = correct_version(w)
        is_divisible2 = student_version(w)
        assert is_divisible1 == is_divisible2
    return

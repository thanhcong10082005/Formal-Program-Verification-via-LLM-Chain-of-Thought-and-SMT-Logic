def any__int(a: int, b: int, c: int) -> bool:
    _contract_result = (a == b + c or b == a + c) or c == a + b
    assert _contract_result == ((a == b + c or b == a + c) or c == a + b)
    return _contract_result

def add(x: int, y: int) -> int:
    z = x + y
    _contract_result = z
    assert _contract_result == x + y
    return _contract_result

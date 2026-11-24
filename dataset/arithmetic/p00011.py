def is__equal__to__sum__even(n: int) -> bool:
    _contract_result = n % 2 == 0 and n >= 8
    assert _contract_result == (n % 2 == 0 and n >= 8)
    return _contract_result

from pkg import target


def test_nonempty():
    assert target([4, 5]) == [4]


def test_none_is_not_a_sequence():
    try:
        target(None)
    except TypeError:
        return
    raise AssertionError("None must retain TypeError")

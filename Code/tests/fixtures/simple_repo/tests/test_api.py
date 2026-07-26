from pkg.api import Box, dispatch, public


def test_public_and_dispatch():
    assert public([]) == []
    assert public([1]) == [1]
    assert dispatch("normalize", Box([1])) == [1]


def test_reflected_addition():
    assert 0 + Box([1])

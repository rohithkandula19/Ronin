import pytest

from quill.slug import slugify


def test_slugifies_a_simple_title():
    assert slugify("Hello World") == "hello-world"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Hello, World!", "hello-world"),
        ("Ronin  vs   Robots", "ronin-vs-robots"),
        ("  --Draft: part 2--  ", "draft-part-2"),
        ("C++ for cats", "c-for-cats"),
    ],
)
def test_slugify_collapses_and_trims_separators(title, expected):
    assert slugify(title) == expected

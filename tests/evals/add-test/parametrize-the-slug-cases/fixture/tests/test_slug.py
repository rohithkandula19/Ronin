from quill.slug import slugify


def test_slugifies_a_simple_title():
    assert slugify("Hello World") == "hello-world"

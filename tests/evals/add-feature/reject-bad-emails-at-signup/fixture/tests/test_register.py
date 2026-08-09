from signup.register import Store, register


def test_stores_the_user():
    store = Store()
    register(store, "ada@example.com", "Ada")
    assert store.all() == [{"email": "ada@example.com", "name": "Ada"}]


def test_normalises_the_address():
    store = Store()
    user = register(store, "  Ada@Example.COM  ", "  Ada  ")
    assert user["email"] == "ada@example.com"
    assert user["name"] == "Ada"

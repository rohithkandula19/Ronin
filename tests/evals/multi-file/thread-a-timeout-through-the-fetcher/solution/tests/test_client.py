import unittest

from fetchkit import CachingTransport, Client, FakeTransport, Response, Transport, get_with_retry
from fetchkit.transport import DEFAULT_TIMEOUT, Request


class FlakyTransport(Transport):
    """Answers 503 for the first *failures* sends, then 200."""

    def __init__(self, failures: int, body: str = "ok"):
        self.failures = failures
        self.body = body
        self.calls = 0

    def send(self, request: Request, timeout: float = DEFAULT_TIMEOUT) -> Response:
        self.calls += 1
        if self.calls <= self.failures:
            return Response(503, "unavailable")
        return Response(200, self.body)


def fake():
    return FakeTransport(
        responses={"/things": Response(200, "[]"), "/slow": Response(200, "late")},
        latency={"/slow": 30.0},
    )


class ClientTests(unittest.TestCase):
    def test_a_get_reaches_the_transport(self):
        transport = fake()
        response = Client(transport).get("/things")
        self.assertEqual((response.status, response.body), (200, "[]"))
        self.assertEqual(transport.log[0]["path"], "/things")

    def test_base_headers_are_merged_and_sorted(self):
        transport = fake()
        client = Client(transport, base_headers={"accept": "json"})
        client.get("/things", headers={"x-trace": "7"})
        self.assertEqual(transport.log[0]["method"], "GET")

    def test_a_missing_path_is_a_404(self):
        self.assertEqual(Client(fake()).get("/nope").status, 404)

    def test_the_default_timeout_is_what_the_transport_sees(self):
        transport = fake()
        Client(transport).get("/things")
        self.assertEqual(transport.log[0]["timeout"], DEFAULT_TIMEOUT)

    def test_a_path_slower_than_the_timeout_gives_504(self):
        self.assertEqual(Client(fake()).get("/slow").status, 504)


class CachingTests(unittest.TestCase):
    def test_a_repeated_get_is_served_from_the_cache(self):
        inner = fake()
        transport = CachingTransport(inner)
        client = Client(transport)
        self.assertEqual(client.get("/things").body, "[]")
        self.assertEqual(client.get("/things").body, "[]")
        self.assertEqual(transport.hits, 1)
        self.assertEqual(len(inner.log), 1)

    def test_a_post_is_never_cached(self):
        inner = fake()
        client = Client(CachingTransport(inner))
        client.post("/things")
        client.post("/things")
        self.assertEqual(len(inner.log), 2)


class RetryTests(unittest.TestCase):
    def test_it_stops_as_soon_as_the_server_is_happy(self):
        transport = FlakyTransport(failures=2)
        response = get_with_retry(Client(transport), "/things", attempts=3)
        self.assertEqual((response.status, transport.calls), (200, 3))

    def test_it_gives_up_after_the_attempt_budget(self):
        transport = FlakyTransport(failures=5)
        response = get_with_retry(Client(transport), "/things", attempts=2)
        self.assertEqual((response.status, transport.calls), (503, 2))


if __name__ == "__main__":
    unittest.main()

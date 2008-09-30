from landscape.tests.helpers import LandscapeTest, FakeRemoteBrokerHelper

from twisted.internet.defer import succeed, fail

from landscape.lib.bpickle import dumps
from landscape.lib.fetch import fetch
from landscape.broker.ping import PingClient, Pinger
from landscape.broker.registration import Identity


class FakePageGetter(object):
    """An fake web client."""

    def __init__(self, response):
        self.response = response
        self.fetches = []

    def get_page(self, url, post, headers, data):
        """
        A method which is supposed to act like a limited version of
        L{landscape.lib.fetch.fetch}.

        Record attempts to get pages, and return a deferred with pre-cooked
        data.
        """
        self.fetches.append((url, post, headers, data))
        return dumps(self.response)

    def failing_get_page(self, url, post, headers, data):
        """
        A method which is supposed to act like a limited version of
        L{landscape.lib.fetch.fetch}.

        Record attempts to get pages, and return a deferred with pre-cooked
        data.
        """
        raise AssertionError("That's a failure!")


class PingClientTest(LandscapeTest):

    helpers = [FakeRemoteBrokerHelper]

    def test_default_get_page(self):
        """
        The C{get_page} argument to L{PingClient} should be optional, and
        default to L{twisted.web.client.getPage}.
        """
        client = PingClient(None, None, None)
        self.assertEqual(client.get_page, fetch)

    def test_ping(self):
        """
        L{PingClient} should be able to send a web request to a specified URL
        about a particular insecure ID.
        """
        client = FakePageGetter(None)
        self.broker_service.identity.insecure_id = 10
        url = "http://localhost/ping"
        pinger = PingClient(self.broker_service.reactor, url,
                            self.broker_service.identity,
                            get_page=client.get_page)
        pinger.ping()
        self.assertEquals(
            client.fetches,
            [(url, True, {"Content-Type": "application/x-www-form-urlencoded"},
              "insecure_id=10")])

    def test_ping_no_insecure_id(self):
        """
        If a L{PingClient} does not have an insecure-id yet, then the ping
        should not happen.
        """
        client = FakePageGetter(None)
        url = "http://localhost/ping"
        pinger = PingClient(self.broker_service.reactor,
                            url, self.broker_service.identity,
                            get_page=client.get_page)
        d = pinger.ping()
        d.addCallback(self.assertEqual, False)
        self.assertEquals(client.fetches, [])

    def test_respond(self):
        """
        The L{PingClient.ping} fire the Deferred it returns with True if the
        web request indicates that the computer has messages.
        """
        self.broker_service.identity.insecure_id = 23
        client = FakePageGetter({"messages": True})
        pinger = PingClient(self.broker_service.reactor,
                            None, self.broker_service.identity,
                            get_page=client.get_page)
        d = pinger.ping()
        d.addCallback(self.assertEqual, True)

    def test_errback(self):
        """
        If a L{PingClient} does not have an insecure-id yet, then the ping
        should not happen.
        """
        self.broker_service.identity.insecure_id = 23
        client = FakePageGetter(None)
        url = "http://localhost/ping"
        pinger = PingClient(self.broker_service.reactor,
                            url, self.broker_service.identity,
                            get_page=client.failing_get_page)
        d = pinger.ping()
        failures = []
        def errback(failure):
            failures.append(failure)
        d.addErrback(errback)
        self.assertEquals(len(failures), 1)
        self.assertEquals(failures[0].getErrorMessage(), "That's a failure!")
        self.assertEquals(failures[0].type, AssertionError)


class FakePingClient(object):

    def __init__(self):
        self.response = False
        self.pings = 0

    def ping(self):
        self.pings += 1
        return succeed(self.response)


class PingerTest(LandscapeTest):

    helpers = [FakeRemoteBrokerHelper]

    # Tell the Plugin helper to not add a MessageExchange plugin, to interfere
    # with our code which asserts stuff about when *our* plugin fires
    # exchanges.
    install_exchanger = False

    def setUp(self):
        super(PingerTest, self).setUp()
        self.url = "http://localhost:8081/whatever"
        self.ping_client = FakePingClient()
        def factory(reactor, url, insecure_id):
            self.ping_client.url = url
            self.ping_client.insecure_id = insecure_id
            return self.ping_client
        self.pinger = Pinger(self.broker_service.reactor,
                             self.url, self.broker_service.identity,
                             self.broker_service.exchanger,
                             interval=10, ping_client_factory=factory)

    def test_default_ping_client(self):
        """
        The C{ping_client_factory} argument to L{Pinger} should be optional,
        and default to L{PingClient}.
        """
        pinger = Pinger(self.broker_service.reactor, "http://foo.com/",
                        self.broker_service.identity,
                        self.broker_service.exchanger)
        self.assertEqual(pinger.ping_client_factory, PingClient)

    def test_occasional_ping(self):
        """
        The L{Pinger} should be able to occasionally ask if there are
        messages.
        """
        self.pinger.start()
        self.broker_service.identity.insecure_id = 23
        self.broker_service.reactor.advance(9)
        self.assertEquals(self.ping_client.pings, 0)
        self.broker_service.reactor.advance(1)
        self.assertEquals(self.ping_client.pings, 1)

    def test_set_insecure_id_message(self):
        """
        L{Pinger} should register a handler for the 'set-id' message
        so that it can start pinging when an insecure-id has been
        received.
        """
        self.pinger.start()
        self.broker_service.identity.insecure_id = 42
        self.broker_service.reactor.advance(10)
        self.assertEquals(self.ping_client.pings, 1)

    def test_load_insecure_id(self):
        """
        If the insecure-id has already been saved when the plugin is
        registered, it should immediately start pinging.
        """
        self.broker_service.identity.insecure_id = 42
        self.pinger.start()
        self.broker_service.reactor.advance(10)
        self.assertEqual(self.ping_client.pings, 1)

    def test_response(self):
        """
        When a ping indicates there are messages, an exchange should occur.
        """
        self.pinger.start()
        self.broker_service.identity.insecure_id = 42
        exchanged = []
        self.ping_client.response = True

        # 70 = ping delay + urgent exchange delay
        self.broker_service.reactor.advance(70)

        self.assertEquals(len(self.broker_service.transport.payloads), 1)

    def test_negative_response(self):
        """
        When a ping indicates there are no messages, no exchange should occur.
        """
        self.pinger.start()
        self.broker_service.identity.insecure_id = 42
        exchanged = []
        self.ping_client.response = False
        self.broker_service.reactor.advance(10)
        self.assertEquals(len(self.broker_service.transport.payloads), 0)

    def test_ping_error(self):
        """
        When the web interaction fails for some reason, a message
        should be logged.
        """
        self.log_helper.ignore_errors(ZeroDivisionError)
        self.pinger.start()
        self.broker_service.identity.insecure_id = 42

        def bad_ping():
            return fail(ZeroDivisionError("Couldn't fetch page"))
        self.ping_client.ping = bad_ping

        self.broker_service.reactor.advance(10)

        log = self.logfile.getvalue()
        self.assertTrue("Error contacting ping server at "
                        "http://localhost:8081/whatever" in log)
        self.assertTrue("ZeroDivisionError" in log)
        self.assertTrue("Couldn't fetch page" in log)

    def test_get_interval(self):
        self.assertEquals(self.pinger.get_interval(), 10)

    def test_set_intervals_handling(self):
        self.pinger.start()

        self.broker_service.reactor.fire("message",
                                         {"type": "set-intervals", "ping": 73})
        self.assertEquals(self.pinger.get_interval(), 73)

        # The server may set specific intervals only, not including the ping.
        self.broker_service.reactor.fire("message", {"type": "set-intervals"})
        self.assertEquals(self.pinger.get_interval(), 73)

        self.broker_service.identity.insecure_id = 23
        self.broker_service.reactor.advance(72)
        self.assertEquals(self.ping_client.pings, 0)
        self.broker_service.reactor.advance(1)
        self.assertEquals(self.ping_client.pings, 1)
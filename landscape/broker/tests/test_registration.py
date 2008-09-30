import logging

from landscape.broker.registration import (
    RegistrationHandler, Identity, InvalidCredentialsError)

from landscape.tests.helpers import LandscapeTest, ExchangeHelper


class RegistrationTest(LandscapeTest):

    helpers = [ExchangeHelper]

    def setUp(self):
        super(RegistrationTest, self).setUp()
        self.config = self.broker_service.config
        self.identity = self.broker_service.identity
        self.handler = self.broker_service.registration
        logging.getLogger().setLevel(logging.INFO)

    def mock_gethostname(self, replay=True):
        gethostname_mock = self.mocker.replace("socket.gethostname")
        gethostname_mock()
        self.mocker.result("ooga")
        if replay:
            self.mocker.replay()

    def check_persist_property(self, attr, persist_name):
        value = "VALUE"
        self.assertEquals(getattr(self.identity, attr), None,
                          "%r attribute should default to None, not %r" %
                          (attr, getattr(self.identity, attr)))
        setattr(self.identity, attr, value)
        self.assertEquals(getattr(self.identity, attr), value,
                          "%r attribute should be %r, not %r" %
                          (attr, value, getattr(self.identity, attr)))
        self.assertEquals(self.persist.get(persist_name), value,
                          "%r not set to %r in persist" % (persist_name, value))

    def check_config_property(self, attr):
        value = "VALUE"
        setattr(self.config, attr, value)
        self.assertEquals(getattr(self.identity, attr), value,
                          "%r attribute should be %r, not %r" %
                          (attr, value, getattr(self.identity, attr)))

    def test_secure_id(self):
        self.check_persist_property("secure_id",
                                    "registration.secure-id")

    def test_insecure_id(self):
        self.check_persist_property("insecure_id",
                                    "registration.insecure-id")

    def test_computer_title(self):
        self.check_config_property("computer_title")

    def test_account_name(self):
        self.check_config_property("account_name")

    def test_registration_password(self):
        self.check_config_property("registration_password")

    def test_server_initiated_id_changing(self):
        """
        The server must be able to ask a client to change its secure
        and insecure ids even if no requests were sent.
        """
        self.reactor.fire("message",
                          {"type": "set-id", "id": "abc", "insecure-id": "def"})
        self.assertEquals(self.identity.secure_id, "abc")
        self.assertEquals(self.identity.insecure_id, "def")

    def test_registration_done_event(self):
        """
        When new ids are received from the server, a "registration-done"
        event is fired.
        """
        reactor_mock = self.mocker.patch(self.reactor)
        reactor_mock.fire("registration-done")
        self.mocker.replay()
        self.reactor.fire("message",
                          {"type": "set-id", "id": "abc", "insecure-id": "def"})

    def test_unknown_id(self):
        self.identity.secure_id = "old_id"
        self.identity.insecure_id = "old_id"
        self.mstore.set_accepted_types(["register"])
        self.reactor.fire("message", {"type": "unknown-id"})
        self.assertEquals(self.identity.secure_id, None)
        self.assertEquals(self.identity.insecure_id, None)

    def test_should_register(self):
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.assertTrue(self.handler.should_register())

    def test_should_register_with_existing_id(self):
        self.mstore.set_accepted_types(["register"])
        self.identity.secure_id = "secure"
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.assertFalse(self.handler.should_register())

    def test_should_register_without_computer_title(self):
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = None
        self.assertFalse(self.handler.should_register())

    def test_should_register_without_account_name(self):
        self.mstore.set_accepted_types(["register"])
        self.config.account_name = None
        self.assertFalse(self.handler.should_register())

    def test_should_register_with_unaccepted_message(self):
        self.assertFalse(self.handler.should_register())

    def test_queue_message_on_exchange(self):
        """
        When a computer_title and account_name are available, no
        secure_id is set, and an exchange is about to happen,
        queue a registration message.
        """
        self.mock_gethostname()
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        self.assertMessages(self.mstore.get_pending_messages(),
                            [{"type": "register",
                              "computer_title": "Computer Title",
                              "account_name": "account_name",
                              "registration_password": None,
                              "hostname": "ooga"}
                            ])
        self.assertEquals(self.logfile.getvalue().strip(),
                          "INFO: Queueing message to register with account "
                          "'account_name' without a password.")

    def test_queue_message_on_exchange_with_password(self):
        """If a registration password is available, we pass it on!"""
        self.mock_gethostname()
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_password = "SEKRET"
        self.reactor.fire("pre-exchange")
        self.assertMessages(self.mstore.get_pending_messages(),
                            [{"type": "register",
                              "computer_title": "Computer Title",
                              "account_name": "account_name",
                              "registration_password": "SEKRET",
                              "hostname": "ooga"}
                            ])
        self.assertEquals(self.logfile.getvalue().strip(),
                          "INFO: Queueing message to register with account "
                          "'account_name' with a password.")

    def test_queueing_registration_message_resets_message_store(self):
        """
        When a registration message is queued, the store is reset
        entirely, since everything else that was queued is meaningless
        now that we're trying to register again.
        """
        self.mstore.set_accepted_types(["register", "test"])
        self.mstore.add({"type": "test"})
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEquals(len(messages), 1)
        self.assertEquals(messages[0]["type"], "register")

    def test_no_message_when_should_register_is_false(self):
        """If we already have a secure id, do not queue a register message.
        """
        handler_mock = self.mocker.patch(self.handler)
        handler_mock.should_register()
        self.mocker.result(False)

        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"

        # If we didn't fake it, it'd work.  We do that to ensure that
        # all the needed data is in place, and that this method is
        # really what decides if a message is sent or not.  This way
        # we can test it individually.
        self.assertTrue(self.handler.should_register())

        # Now let's see.
        self.mocker.replay()

        self.reactor.fire("pre-exchange")
        self.assertMessages(self.mstore.get_pending_messages(), [])

    def test_registration_failed_event(self):
        """
        The deferred returned by a registration request should fail
        with L{InvalidCredentialsError} if the server responds with a
        failure message.
        """
        reactor_mock = self.mocker.patch(self.reactor)
        reactor_mock.fire("registration-failed")
        self.mocker.replay()
        self.reactor.fire("message",
                          {"type": "registration", "info": "unknown-account"})

    def test_registration_failed_event_not_fired_when_uncertain(self):
        """
        If the data in the registration message isn't what we expect,
        the event isn't fired.
        """
        reactor_mock = self.mocker.patch(self.reactor)
        reactor_mock.fire("registration-failed")
        self.mocker.count(0)
        self.mocker.replay()
        self.reactor.fire("message",
                          {"type": "registration", "info": "blah-blah"})

    def test_register_resets_ids(self):
        self.identity.secure_id = "foo"
        self.identity.insecure_id = "bar"
        self.handler.register()
        self.assertEquals(self.identity.secure_id, None)
        self.assertEquals(self.identity.insecure_id, None)

    def test_register_calls_urgent_exchange(self):
        exchanger_mock = self.mocker.patch(self.exchanger)
        exchanger_mock.exchange()
        self.mocker.passthrough()
        self.mocker.replay()
        self.handler.register()

    def test_register_deferred_called_on_done(self):
        # We don't want informational messages.
        self.logger.setLevel(logging.WARNING)

        calls = [0]
        d = self.handler.register()
        def add_call(result):
            self.assertEquals(result, None)
            calls[0] += 1
        d.addCallback(add_call)

        # This should somehow callback the deferred.
        self.reactor.fire("message",
                          {"type": "set-id", "id": "abc", "insecure-id": "def"})

        self.assertEquals(calls, [1])

        # Doing it again to ensure that the deferred isn't called twice.
        self.reactor.fire("message",
                          {"type": "set-id", "id": "abc", "insecure-id": "def"})

        self.assertEquals(calls, [1])

        self.assertEquals(self.logfile.getvalue(), "")

    def test_resynchronize_fired_when_registration_done(self):

        results = []
        def append():
            results.append(True)
        self.reactor.call_on("resynchronize-clients", append)

        self.handler.register()

        # This should somehow callback the deferred.
        self.reactor.fire("message",
                          {"type": "set-id", "id": "abc", "insecure-id": "def"})

        self.assertEquals(results, [True])

    def test_register_deferred_called_on_failed(self):
        # We don't want informational messages.
        self.logger.setLevel(logging.WARNING)

        calls = [0]
        d = self.handler.register()
        def add_call(failure):
            exception = failure.value
            self.assertTrue(isinstance(exception, InvalidCredentialsError))
            calls[0] += 1
        d.addErrback(add_call)

        # This should somehow callback the deferred.
        self.reactor.fire("message",
                          {"type": "registration", "info": "unknown-account"})

        self.assertEquals(calls, [1])

        # Doing it again to ensure that the deferred isn't called twice.
        self.reactor.fire("message",
                          {"type": "registration", "info": "unknown-account"})

        self.assertEquals(calls, [1])

        self.assertEquals(self.logfile.getvalue(), "")

    def test_exchange_done_calls_exchange(self):
        exchanger_mock = self.mocker.patch(self.exchanger)
        exchanger_mock.exchange()
        self.mocker.passthrough()
        self.mocker.replay()

        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("exchange-done")

    def test_exchange_done_wont_call_exchange_when_just_tried(self):
        exchanger_mock = self.mocker.patch(self.exchanger)
        exchanger_mock.exchange()
        self.mocker.count(0)
        self.mocker.replay()

        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        self.reactor.fire("exchange-done")
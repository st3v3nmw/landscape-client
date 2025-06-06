import json
import logging
import socket
from unittest import mock

from landscape.client.broker.registration import Identity
from landscape.client.broker.registration import RegistrationError
from landscape.client.broker.tests.helpers import BrokerConfigurationHelper
from landscape.client.broker.tests.helpers import RegistrationHelper
from landscape.client.tests.helpers import LandscapeTest
from landscape.lib.persist import Persist


class IdentityTest(LandscapeTest):

    helpers = [BrokerConfigurationHelper]

    def setUp(self):
        super().setUp()
        self.persist = Persist(filename=self.makePersistFile())
        self.identity = Identity(self.config, self.persist)

    def check_persist_property(self, attr, persist_name):
        value = "VALUE"
        self.assertEqual(
            getattr(self.identity, attr),
            None,
            f"{attr!r} attribute should default to None, "
            f"not {getattr(self.identity, attr)!r}",
        )
        setattr(self.identity, attr, value)
        self.assertEqual(
            getattr(self.identity, attr),
            value,
            f"{attr!r} attribute should be {value!r}, "
            f"not {getattr(self.identity, attr)!r}",
        )
        self.assertEqual(
            self.persist.get(persist_name),
            value,
            f"{persist_name!r} not set to {value!r} in persist",
        )

    def check_config_property(self, attr):
        value = "VALUE"
        setattr(self.config, attr, value)
        self.assertEqual(
            getattr(self.identity, attr),
            value,
            f"{attr!r} attribute should be {value!r}, "
            f"not {getattr(self.identity, attr)!r}",
        )

    def test_secure_id(self):
        self.check_persist_property("secure_id", "registration.secure-id")

    def test_secure_id_as_unicode(self):
        """secure-id is expected to be retrieved as unicode."""
        self.identity.secure_id = b"spam"
        self.assertEqual(self.identity.secure_id, "spam")

    def test_insecure_id(self):
        self.check_persist_property("insecure_id", "registration.insecure-id")

    def test_computer_title(self):
        self.check_config_property("computer_title")

    def test_account_name(self):
        self.check_config_property("account_name")

    def test_registration_key(self):
        self.check_config_property("registration_key")

    def test_client_tags(self):
        self.check_config_property("tags")

    def test_access_group(self):
        self.check_config_property("access_group")

    def test_hostagent_uid(self):
        self.check_config_property("hostagent_uid")


class RegistrationHandlerTestBase(LandscapeTest):

    helpers = [RegistrationHelper]

    def setUp(self):
        super().setUp()
        logging.getLogger().setLevel(logging.INFO)
        self.hostname = "ooga.local"
        self.addCleanup(setattr, socket, "getfqdn", socket.getfqdn)
        socket.getfqdn = lambda: self.hostname


class RegistrationHandlerTest(RegistrationHandlerTestBase):
    def test_server_initiated_id_changing(self):
        """
        The server must be able to ask a client to change its secure
        and insecure ids even if no requests were sent.
        """
        self.exchanger.handle_message(
            {"type": b"set-id", "id": b"abc", "insecure-id": b"def"},
        )
        self.assertEqual(self.identity.secure_id, "abc")
        self.assertEqual(self.identity.insecure_id, "def")

    def test_registration_done_event(self):
        """
        When new ids are received from the server, a "registration-done"
        event is fired.
        """
        reactor_fire_mock = self.reactor.fire = mock.Mock()
        self.exchanger.handle_message(
            {"type": b"set-id", "id": b"abc", "insecure-id": b"def"},
        )
        reactor_fire_mock.assert_any_call("registration-done")

    def test_unknown_id(self):
        self.identity.secure_id = "old_id"
        self.identity.insecure_id = "old_id"
        self.mstore.set_accepted_types(["register"])
        self.exchanger.handle_message({"type": b"unknown-id"})
        self.assertEqual(self.identity.secure_id, None)
        self.assertEqual(self.identity.insecure_id, None)

    def test_unknown_id_with_clone(self):
        """
        If the server reports us that we are a clone of another computer, then
        make sure we handle it
        """
        self.config.computer_title = "Wu"
        self.mstore.set_accepted_types(["register"])
        self.exchanger.handle_message(
            {"type": b"unknown-id", "clone-of": "Wu"},
        )
        self.assertIn(
            "Client is clone of computer Wu",
            self.logfile.getvalue(),
        )

    def test_clone_secure_id_saved(self):
        """
        Make sure that secure id is saved when theres a clone and existing
        value is cleared out
        """
        secure_id = "foo"
        self.identity.secure_id = secure_id
        self.config.computer_title = "Wu"
        self.mstore.set_accepted_types(["register"])
        self.exchanger.handle_message(
            {"type": b"unknown-id", "clone-of": "Wu"},
        )
        self.assertEqual(self.handler._clone_secure_id, secure_id)
        self.assertIsNone(self.identity.secure_id)

    def test_clone_id_in_message(self):
        """
        Make sure that the clone id is present in the registration message
        """
        secure_id = "foo"
        self.identity.secure_id = secure_id
        self.config.computer_title = "Wu"
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")  # Note this is only for later api
        self.exchanger.handle_message(
            {"type": b"unknown-id", "clone-of": "Wu"},
        )
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(messages[0]["clone_secure_id"], secure_id)

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
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(1, len(messages))
        self.assertEqual("register", messages[0]["type"])
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "INFO: Queueing message to register with account "
            "'account_name' without a password.",
        )

    @mock.patch("landscape.client.broker.registration.get_vm_info")
    def test_queue_message_on_exchange_with_vm_info(self, get_vm_info_mock):
        """
        When a computer_title and account_name are available, no
        secure_id is set, and an exchange is about to happen,
        queue a registration message with VM information.
        """
        get_vm_info_mock.return_value = b"vmware"
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(b"vmware", messages[0]["vm-info"])
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "INFO: Queueing message to register with account "
            "'account_name' without a password.",
        )
        get_vm_info_mock.assert_called_once_with()

    @mock.patch("landscape.client.broker.registration.get_container_info")
    def test_queue_message_on_exchange_with_lxc_container(
        self,
        get_container_info_mock,
    ):
        """
        If the client is running in an LXC container, the information is
        included in the registration message.
        """
        get_container_info_mock.return_value = "lxc"
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual("lxc", messages[0]["container-info"])
        get_container_info_mock.assert_called_once_with()

    def test_queue_message_on_exchange_with_password(self):
        """If a registration password is available, we pass it on!"""
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_key = "SEKRET"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        password = messages[0]["registration_password"]
        self.assertEqual("SEKRET", password)
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "INFO: Queueing message to register with account "
            "'account_name' with a password.",
        )

    def test_queue_message_on_exchange_with_tags(self):
        """
        If the admin has defined tags for this computer, we send them to the
        server.
        """
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_key = "SEKRET"
        self.config.tags = "computer,tag"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual("computer,tag", messages[0]["tags"])
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "INFO: Queueing message to register with account "
            "'account_name' and tags computer,tag with a "
            "password.",
        )

    def test_queue_message_on_exchange_with_invalid_tags(self):
        """
        If the admin has defined tags for this computer, but they are not
        valid, we drop them, and report an error.
        """
        self.log_helper.ignore_errors("Invalid tags provided for registration")
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_key = "SEKRET"
        self.config.tags = "<script>alert()</script>"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertIs(None, messages[0]["tags"])
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "ERROR: Invalid tags provided for registration.\n    "
            "INFO: Queueing message to register with account "
            "'account_name' with a password.",
        )

    def test_queue_message_on_exchange_with_unicode_tags(self):
        """
        If the admin has defined tags for this computer, we send them to the
        server.
        """
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_key = "SEKRET"
        self.config.tags = "prova\N{LATIN SMALL LETTER J WITH CIRCUMFLEX}o"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        expected = "prova\N{LATIN SMALL LETTER J WITH CIRCUMFLEX}o"
        self.assertEqual(expected, messages[0]["tags"])

        logs = self.logfile.getvalue().strip()
        logs = logs.encode("utf-8")

        self.assertEqual(
            logs,
            b"INFO: Queueing message to register with account "
            b"'account_name' and tags prova\xc4\xb5o "
            b"with a password.",
        )

    def test_queue_message_on_exchange_with_access_group(self):
        """
        If the admin has defined an access_group for this computer, we send
        it to the server.
        """
        self.mstore.set_accepted_types(["register"])
        self.config.account_name = "account_name"
        self.config.access_group = "dinosaurs"
        self.config.tags = "server,london"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual("dinosaurs", messages[0]["access_group"])
        self.assertEqual(
            self.logfile.getvalue().strip(),
            "INFO: Queueing message to register with account "
            "'account_name' in access group 'dinosaurs' and "
            "tags server,london without a password.",
        )

    def test_queue_message_on_exchange_with_empty_access_group(self):
        """
        If the access_group is "", then the outgoing message does not define
        an "access_group" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.config.access_group = ""
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        # Make sure the key does not appear in the outgoing message.
        self.assertNotIn("access_group", messages[0])

    def test_queue_message_on_exchange_with_none_access_group(self):
        """
        If the access_group is None, then the outgoing message does not define
        an "access_group" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.config.access_group = None
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        # Make sure the key does not appear in the outgoing message.
        self.assertNotIn("access_group", messages[0])

    def test_queue_message_on_exchange_with_hostagent_uid(self):
        """
        If the admin has defined a hostagent_uid for this computer, we send
        it to the server.
        """
        self.mstore.set_accepted_types(["register"])
        # hostagent_uid is introduced in the 3.3 message schema
        self.mstore.set_server_api(b"3.3")
        self.config.account_name = "account_name"
        self.config.hostagent_uid = "dinosaur computer"
        self.config.tags = "server,london"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual("dinosaur computer", messages[0]["hostagent_uid"])

    def test_queue_message_on_exchange_with_empty_hostagent_uid(self):
        """
        If the hostagent_uid is "", then the outgoing message does not define
        a "hostagent_uid" key.
        """
        self.mstore.set_accepted_types(["register"])
        # hostagent_uid is introduced in the 3.3 message schema
        self.mstore.set_server_api(b"3.3")
        self.config.hostagent_uid = ""
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("hostagent_uid", messages[0])

    def test_queue_message_on_exchange_with_none_hostagent_uid(self):
        """
        If the hostagent_uid is None, then the outgoing message does not define
        a "hostagent_uid" key.
        """
        self.mstore.set_accepted_types(["register"])
        # hostagent_uid is introduced in the 3.3 message schema
        self.mstore.set_server_api(b"3.3")
        self.config.hostagent_uid = None
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("hostagent_uid", messages[0])

    def test_queue_message_on_exchange_with_installation_request_id(self):
        """
        If the admin has defined a installation_request_id for this computer,
        we send it to the server.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.account_name = "account_name"
        self.config.installation_request_id = "installed-according-to-plan"
        self.config.tags = "server,london"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(
            "installed-according-to-plan",
            messages[0]["installation_request_id"],
        )

    def test_queue_message_on_exchange_and_empty_installation_request_id(self):
        """
        If the installation_request_id is "", then the outgoing message
        does not define an "installation_request_id" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.installation_request_id = ""
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("installation_request_id", messages[0])

    def test_queue_message_on_exchange_with_none_installation_request_id(self):
        """
        If the installation_request_id is None, then the outgoing message
        does not define an "installation_request_id" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.installation_request_id = None
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("installation_request_id", messages[0])

    def test_queue_message_on_exchange_with_authenticated_attach_code(self):
        """
        If the admin has defined a authenticated_attach_code for this
        computer, we send it to the server.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.account_name = "account_name"
        self.config.authenticated_attach_code = "hushhushsupersecretcode"
        self.config.tags = "server,london"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(
            "hushhushsupersecretcode",
            messages[0]["authenticated_attach_code"],
        )

    def test_queue_message_on_exchange_empty_authenticated_attach_code(self):
        """
        If the authenticated_attach_code is "", then the outgoing message
        does not define an "authenticated_attach_code" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.authenticated_attach_code = ""
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("authenticated_attach_code", messages[0])

    def test_queue_message_on_exchange_none_authenticated_attach_code(self):
        """
        If the authenticated_attach_code is None, then the outgoing message
        does not define an "authenticated_attach_code" key.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.authenticated_attach_code = None
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertNotIn("authenticated_attach_code", messages[0])

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
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "register")

    def test_no_message_when_should_register_is_false(self):
        """If we already have a secure id, do not queue a register message."""
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"

        # If we didn't fake it, it'd work.  We do that to ensure that
        # all the needed data is in place, and that this method is
        # really what decides if a message is sent or not.  This way
        # we can test it individually.
        self.assertTrue(self.handler.should_register())

        handler_mock = self.handler.should_register = mock.Mock()
        handler_mock.return_value = False

        self.reactor.fire("pre-exchange")
        self.assertMessages(self.mstore.get_pending_messages(), [])
        handler_mock.assert_called_once_with()

    def test_registration_failed_event_unknown_account(self):
        """
        The deferred returned by a registration request should fail
        if the server responds with a failure message because credentials are
        wrong.
        """
        reactor_fire_mock = self.reactor.fire = mock.Mock()
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"unknown-account"},
        )
        reactor_fire_mock.assert_called_with(
            "registration-failed",
            reason="unknown-account",
        )

    def test_registration_failed_event_max_pending_computers(self):
        """
        The deferred returned by a registration request should fail
        if the server responds with a failure message because the max number of
        pending computers have been reached.
        """
        reactor_fire_mock = self.reactor.fire = mock.Mock()
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"max-pending-computers"},
        )
        reactor_fire_mock.assert_called_with(
            "registration-failed",
            reason="max-pending-computers",
        )

    def test_registration_failed_event_not_fired_when_uncertain(self):
        """
        If the data in the registration message isn't what we expect,
        the event isn't fired.
        """
        reactor_fire_mock = self.reactor.fire = mock.Mock()
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"blah-blah"},
        )
        for name, args, kwargs in reactor_fire_mock.mock_calls:
            self.assertNotEqual("registration-failed", args[0])

    def test_register_resets_ids(self):
        self.identity.secure_id = "foo"
        self.identity.insecure_id = "bar"
        self.handler.register()
        self.assertEqual(self.identity.secure_id, None)
        self.assertEqual(self.identity.insecure_id, None)

    def test_register_calls_urgent_exchange(self):
        self.exchanger.exchange = mock.Mock(wraps=self.exchanger.exchange)
        self.handler.register()
        self.exchanger.exchange.assert_called_once_with()

    def test_register_deferred_called_on_done(self):
        # We don't want informational messages.
        self.logger.setLevel(logging.WARNING)

        calls = [0]
        d = self.handler.register()

        def add_call(result):
            self.assertEqual(result, None)
            calls[0] += 1

        d.addCallback(add_call)

        # This should somehow callback the deferred.
        self.exchanger.handle_message(
            {"type": b"set-id", "id": b"abc", "insecure-id": b"def"},
        )

        self.assertEqual(calls, [1])

        # Doing it again to ensure that the deferred isn't called twice.
        self.exchanger.handle_message(
            {"type": b"set-id", "id": b"abc", "insecure-id": b"def"},
        )

        self.assertEqual(calls, [1])

        self.assertEqual(self.logfile.getvalue(), "")

    def test_resynchronize_fired_when_registration_done(self):
        """
        When we call C{register} this should trigger a "resynchronize-clients"
        event with global scope.
        """
        results = []

        def append(scopes=None):
            results.append(scopes)

        self.reactor.call_on("resynchronize-clients", append)

        self.handler.register()

        # This should somehow callback the deferred.
        self.exchanger.handle_message(
            {"type": b"set-id", "id": b"abc", "insecure-id": b"def"},
        )

        self.assertEqual(results, [None])

    def test_register_deferred_called_on_failed_unknown_account(self):
        """
        The registration errback is called on failures when credentials are
        invalid.
        """
        # We don't want informational messages.
        self.logger.setLevel(logging.WARNING)

        calls = []
        d = self.handler.register()

        def add_call(failure):
            exception = failure.value
            self.assertTrue(isinstance(exception, RegistrationError))
            self.assertEqual("unknown-account", str(exception))
            calls.append(True)

        d.addErrback(add_call)

        # This should somehow callback the deferred.
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"unknown-account"},
        )

        self.assertEqual(calls, [True])

        # Doing it again to ensure that the deferred isn't called twice.
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"unknown-account"},
        )

        self.assertEqual(calls, [True])

        self.assertEqual(self.logfile.getvalue(), "")

    def test_register_deferred_called_on_failed_max_pending_computers(self):
        """
        The registration errback is called on failures when max number of
        pending computers has been reached.
        """
        # We don't want informational messages.
        self.logger.setLevel(logging.WARNING)

        calls = []
        d = self.handler.register()

        def add_call(failure):
            exception = failure.value
            self.assertTrue(isinstance(exception, RegistrationError))
            self.assertEqual("max-pending-computers", str(exception))
            calls.append(True)

        d.addErrback(add_call)

        self.exchanger.handle_message(
            {"type": b"registration", "info": b"max-pending-computers"},
        )

        self.assertEqual(calls, [True])

        # Doing it again to ensure that the deferred isn't called twice.
        self.exchanger.handle_message(
            {"type": b"registration", "info": b"max-pending-computers"},
        )

        self.assertEqual(calls, [True])

        self.assertEqual(self.logfile.getvalue(), "")

    def test_exchange_done_calls_exchange(self):
        self.exchanger.exchange = mock.Mock(wraps=self.exchanger.exchange)
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("exchange-done")
        self.exchanger.exchange.assert_called_once_with()

    def test_exchange_done_wont_call_exchange_when_just_tried(self):
        self.exchanger.exchange = mock.Mock(wraps=self.exchanger.exchange)
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        self.reactor.fire("exchange-done")
        self.assertNot(self.exchanger.exchange.called)

    def test_default_hostname(self):
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.config.registration_key = "SEKRET"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertEqual(socket.getfqdn(), messages[0]["hostname"])

    def test_ubuntu_pro_info_present_in_registration(self):
        """Ubuntu Pro info is included to handle licensing in Server"""
        self.mstore.set_server_api(b"3.3")
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")
        messages = self.mstore.get_pending_messages()
        self.assertIn("ubuntu_pro_info", messages[0])

    @mock.patch("landscape.client.manager.ubuntuproinfo.IS_CORE", new=True)
    def test_ubuntu_pro_info_present_on_core_for_licensing(self):
        """
        Ubuntu Pro info is mocked and sufficient for licensing on Core distros
        during the registration message
        """

        self.mstore.set_server_api(b"3.3")
        self.mstore.set_accepted_types(["register"])
        self.config.computer_title = "Computer Title"
        self.config.account_name = "account_name"
        self.reactor.fire("pre-exchange")

        messages = self.mstore.get_pending_messages()

        # verify the minimum necessary fields that Server expects
        self.assertIn("ubuntu_pro_info", messages[0])
        ubuntu_pro_info = json.loads(messages[0]["ubuntu_pro_info"])
        self.assertIn("effective", ubuntu_pro_info)
        self.assertIn("expires", ubuntu_pro_info)
        contract = ubuntu_pro_info["contract"]
        self.assertIn("landscape", contract["products"])


class JujuRegistrationHandlerTest(RegistrationHandlerTestBase):

    juju_contents = json.dumps(
        {
            "environment-uuid": "DEAD-BEEF",
            "machine-id": "1",
            "api-addresses": "10.0.3.1:17070",
        },
    )

    def test_juju_info_added_when_present(self):
        """
        When information about the Juju environment is found in
        the $data_dir/juju-info.d/ directory, it's included in
        the registration message.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.3")
        self.config.account_name = "account_name"
        self.reactor.fire("run")
        self.reactor.fire("pre-exchange")

        messages = self.mstore.get_pending_messages()
        self.assertEqual(
            {
                "environment-uuid": "DEAD-BEEF",
                "machine-id": "1",
                "api-addresses": ["10.0.3.1:17070"],
            },
            messages[0]["juju-info"],
        )

    def test_juju_info_skipped_with_old_server(self):
        """
        If a server doesn't speak at least 3.3, the juju-info field is
        isn't included in the message.
        """
        self.mstore.set_accepted_types(["register"])
        self.mstore.set_server_api(b"3.2")
        self.config.account_name = "account_name"
        self.reactor.fire("run")
        self.reactor.fire("pre-exchange")

        messages = self.mstore.get_pending_messages()
        self.assertNotIn("juju-info", messages[0])

from landscape.monitor.loadaverage import LoadAverage
from landscape.tests.helpers import LandscapeTest, MonitorHelper
from landscape.tests.mocker import ANY


def get_load_average():
    i = 1
    while True:
        yield (float(i), 1.0, 500.238)
        i += 1


class LoadAveragePluginTest(LandscapeTest):

    helpers = [MonitorHelper]

    def test_real_load_average(self):
        """
        When the load average plugin runs it calls os.getloadavg() to
        retrieve current load average data.  This test makes sure that
        os.getloadavg() is called without failing and that messages
        with the expected datatypes are generated.
        """
        plugin = LoadAverage(create_time=self.reactor.time)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size)

        message = plugin.create_message()
        self.assertTrue("type" in message)
        self.assertEquals(message["type"], "load-average")
        self.assertTrue("load-averages" in message)

        load_averages = message["load-averages"]
        self.assertEquals(len(load_averages), 1)

        load_average = load_averages[0]
        self.assertEquals(load_average[0], self.monitor.step_size)
        self.assertTrue(isinstance(load_average[0], int))
        self.assertTrue(isinstance(load_average[1], float))

    def test_sample_load_average(self):
        """
        Sample data is used to ensure that the load average included
        in the message is calculated correctly.
        """
        get_load_average = lambda: (0.15, 1, 500)
        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size)

        message = plugin.create_message()
        load_averages = message["load-averages"]
        self.assertEquals(len(load_averages), 1)
        self.assertEquals(load_averages[0], (self.monitor.step_size, 0.15))

    def test_ranges_remain_contiguous_after_flush(self):
        """
        The load average plugin uses the accumulate function to queue
        messages.  Timestamps should always be contiguous, and always
        fall on a step boundary.
        """
        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        for i in range(1, 10):
            self.reactor.advance(self.monitor.step_size)
            message = plugin.create_message()
            load_averages = message["load-averages"]
            self.assertEquals(len(load_averages), 1)
            self.assertEquals(load_averages[0][0], self.monitor.step_size * i)

    def test_messaging_flushes(self):
        """
        Duplicate message should never be created.  If no data is
        available, a message with an empty C{load-averages} list is
        expected.
        """
        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size)

        message = plugin.create_message()
        self.assertEquals(len(message["load-averages"]), 1)

        message = plugin.create_message()
        self.assertEquals(len(message["load-averages"]), 0)

    def test_never_exchange_empty_messages(self):
        """
        The plugin will create a message with an empty
        C{load-averages} list when no data is available.  If an empty
        message is created during exchange, it should not be queued.
        """
        self.mstore.set_accepted_types(["load-average"])

        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        self.monitor.exchange()
        self.assertEquals(len(self.mstore.get_pending_messages()), 0)

    def test_exchange_messages(self):
        """
        The load average plugin queues message when manager.exchange()
        is called.  Each message should be aligned to a step boundary;
        messages collected bewteen exchange periods should be
        delivered in a single message.
        """
        self.mstore.set_accepted_types(["load-average"])

        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size * 2)
        self.monitor.exchange()

        self.assertMessages(self.mstore.get_pending_messages(),
                            [{"type": "load-average",
                              "load-averages": [(300, 10.5), (600, 30.5)]}])

    def test_call_on_accepted(self):
        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size * 1)

        remote_broker_mock = self.mocker.replace(self.remote)
        remote_broker_mock.send_message(ANY, urgent=True)
        self.mocker.replay()

        self.reactor.fire(("message-type-acceptance-changed", "load-average"),
                          True)

    def test_no_message_if_not_accepted(self):
        """
        Don't add any messages at all if the broker isn't currently
        accepting their type.
        """
        plugin = LoadAverage(create_time=self.reactor.time,
                             get_load_average=get_load_average().next)
        self.monitor.add(plugin)

        self.reactor.advance(self.monitor.step_size * 2)
        self.monitor.exchange()

        self.mstore.set_accepted_types(["load-average"])
        self.assertMessages(list(self.mstore.get_pending_messages()), [])
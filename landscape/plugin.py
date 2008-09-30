from logging import info, exception

from landscape.lib.bpickle import loads
from landscape.lib.dbus_util import Object, array_to_string
from landscape.log import format_object


class HandlerNotFoundError(Exception):
    """A handler for the given message type was not found."""


class PluginConfigError(Exception):
    """There was an error registering or configuring a plugin."""


class PluginRegistry(object):
    """A central integration point for plugins."""

    def __init__(self):
        self._plugins = []
        self._plugin_names = {}
        self._registered_messages = {}

    def add(self, plugin):
        """Register a plugin.

        The plugin's C{register} method will be called with this registry as
        its argument.

        If the plugin has a C{plugin_name} attribute, it will be possible to
        look up the plugin later with L{get_plugin}.
        """
        info("Registering plugin %s.", format_object(plugin))
        self._plugins.append(plugin)
        if hasattr(plugin, 'plugin_name'):
            self._plugin_names[plugin.plugin_name] = plugin
        plugin.register(self)

    def get_plugins(self):
        """Get the list of plugins."""
        return self._plugins

    def get_plugin(self, name):
        """Get a particular plugin by name."""
        return self._plugin_names[name]

    def register_message(self, type, handler):
        """
        Register interest in a particular type of Landscape server->client
        message.
        """
        self._registered_messages[type] = handler

    def dispatch_message(self, message):
        type = message["type"]
        handler = self._registered_messages.get(type)
        if handler is not None:
            try:
                return handler(message)
            except:
                exception("Error running message handler for type %r: %r"
                          % (type, handler))
        else:
            raise HandlerNotFoundError(type)


class Plugin(object):
    """A convenience for writing plugins.

    This provides a register method which will set up a bunch of
    reactor handlers in the idiomatic way.

    If C{run} is defined on subclasses, it will be called every C{run_interval}
    seconds after being registered.

    @cvar run_interval: The interval, in seconds, to execute the
    C{run} method. If set to C{None}, then C{run} will not be
    scheduled.
    """

    run_interval = 5

    def register(self, registry):
        self.registry = registry
        if hasattr(self, "run") and self.run_interval is not None:
            registry.reactor.call_every(self.run_interval, self.run)



class BrokerPlugin(Object):
    """
    A DBus object which exposes the 'plugin' interface that the Broker expects
    of its clients.
    """
    def __init__(self, bus, registry):
        Object.__init__(self, bus)
        self.registry = registry

    def ping(self):
        return True

    def exit(self):
        from twisted.internet import reactor
        reactor.callLater(0.1, reactor.stop)

    def dispatch_message(self, blob):
        """
        Call the L{PluginRegistry}'s C{dispatch_message} method and return True
        if a message handler was found and False otherwise.
        """
        message = loads(array_to_string(blob))
        try:
            self.registry.dispatch_message(message)
            return True
        except HandlerNotFoundError:
            return False

    def message(self, blob):
        """
        Call L{dispatch_message} with C{blob} and return the result.
        """
        return self.dispatch_message(blob)
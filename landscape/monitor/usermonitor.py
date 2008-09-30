import logging

from landscape.lib.log import log_failure

from landscape.lib.dbus_util import method, get_object, Object
from landscape.monitor.monitor import (MonitorPlugin, BUS_NAME, OBJECT_PATH,
                                       IFACE_NAME)
from landscape.user.changes import UserChanges
from landscape.user.provider import UserProvider


class UserMonitorDBusObject(Object):
    """
    A DBUS object which exposes an API for getting the monitor to detect user
    changes and upload them to the Landscape server.
    """
    object_path = OBJECT_PATH + "/UserMonitor"
    iface_name = IFACE_NAME + ".UserMonitor"
    bus_name = BUS_NAME

    def __init__(self, bus, plugin):
        super(UserMonitorDBusObject, self).__init__(bus)
        self._plugin = plugin

    @method(iface_name)
    def detect_changes(self, operation_id=None):
        return self._plugin.run(operation_id)


class UserMonitor(MonitorPlugin):
    """
    A plugin which monitors the system user databases.
    """

    persist_name = "users"
    run_interval = 3600 # 1 hour

    def __init__(self, provider=None):
        if provider is None:
            provider = UserProvider()
        self._provider = provider

    def register(self, registry):
        super(UserMonitor, self).register(registry)
        self.registry.reactor.call_on("resynchronize", self._resynchronize)
        self.call_on_accepted("users", self._run_detect_changes, None)
        self._dbus_object = UserMonitorDBusObject(registry.bus, self)

    def _resynchronize(self):
        """Resynchronize user and group data."""
        changes = UserChanges(self._persist, self._provider)
        changes.clear()

    def run(self, operation_id=None):
        return self.registry.broker.call_if_accepted(
            "users", self._run_detect_changes, operation_id)

    def _run_detect_changes(self, operation_id=None):
        """
        If changes are detected an C{urgent-exchange} is fired to send
        updates to the server immediately.

        @param operation_id: When present it will be included in the
            C{operation-id} field.
        """
        from landscape.manager.usermanager import UserManagerDBusObject
        remote_service = get_object(self.registry.bus,
            UserManagerDBusObject.bus_name, UserManagerDBusObject.object_path)

        result = remote_service.get_locked_usernames()
        result.addCallback(self._detect_changes, operation_id)
        return result

    def _detect_changes(self, result, operation_id=None):
        def update_snapshot(result):
            changes.snapshot()
            return result

        def log_error(result):
            log_failure(result, "Error occured calling send_message in "
                        "_detect_changes")

        self._provider.locked_users = result
        changes = UserChanges(self._persist, self._provider)
        message = changes.create_diff()
        if message:
            message["type"] = "users"
            if operation_id:
                message["operation-id"] = operation_id
            result = self.registry.broker.send_message(message, urgent=True)
            result.addCallback(update_snapshot)
            result.addErrback(log_error)
            return result
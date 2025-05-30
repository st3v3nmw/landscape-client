"""Deployment code for the sysinfo tool."""
import os
import sys
from argparse import ArgumentTypeError
from logging import Formatter
from logging import getLogger
from logging.handlers import RotatingFileHandler

from twisted.internet.defer import Deferred
from twisted.internet.defer import maybeDeferred
from twisted.python.reflect import namedClass

from landscape import VERSION
from landscape.lib.config import BaseConfiguration
from landscape.sysinfo.sysinfo import format_sysinfo
from landscape.sysinfo.sysinfo import SysInfoPluginRegistry


ALL_PLUGINS = [
    "Load",
    "Disk",
    "Memory",
    "Temperature",
    "Processes",
    "LoggedInUsers",
    "Network",
]


def plugin_list(string: str) -> list[str]:
    """
    Parser for converting a comma separated string of plugin names
    to a list of those plugin names.
    """
    plugins = [plugin.strip() for plugin in string.split(",")]
    valid_plugins = []
    invalid_plugins = []
    for plugin in plugins:
        if plugin in ALL_PLUGINS:
            valid_plugins.append(plugin)
        else:
            invalid_plugins.append(plugin)

    if invalid_plugins:
        raise ArgumentTypeError(invalid_plugins)
    return valid_plugins


class SysInfoConfiguration(BaseConfiguration):
    """Specialized configuration for the Landscape sysinfo tool."""

    version = VERSION

    default_config_filenames = ("/etc/landscape/client.conf",)
    if os.getuid() != 0:
        default_config_filenames += (
            os.path.expanduser("~/.landscape/sysinfo.conf"),
        )
    default_data_dir = "/var/lib/landscape/client/"

    config_section = "sysinfo"

    def __init__(self):
        super().__init__()

        self._command_line_defaults["config"] = None

    def make_parser(self):
        """
        Specialize L{Configuration.make_parser}, adding any
        sysinfo-specific options.
        """
        parser = super().make_parser()

        parser.add_argument(
            "--sysinfo-plugins",
            metavar="PLUGIN_LIST",
            help="Comma-delimited list of sysinfo plugins to "
            "use. Default is to use all plugins.",
            type=plugin_list,
        )

        parser.add_argument(
            "--exclude-sysinfo-plugins",
            metavar="PLUGIN_LIST",
            help="Comma-delimited list of sysinfo plugins to "
            "NOT use. This always take precedence over "
            "plugins to include.",
            type=plugin_list,
        )

        parser.add_argument(
            "--width",
            type=int,
            default=80,
            help="Maximum width for each column of output.",
        )

        parser.epilog = "Default plugins: {}".format(", ".join(ALL_PLUGINS))
        return parser

    def get_plugins(self):
        if self.sysinfo_plugins is None:
            include = ALL_PLUGINS
        else:
            include = self.sysinfo_plugins
        if self.exclude_sysinfo_plugins is None:
            exclude = []
        else:
            exclude = self.exclude_sysinfo_plugins
        plugins = [x for x in include if x not in exclude]
        return [
            namedClass(
                f"landscape.sysinfo.{plugin_name.lower()}.{plugin_name}",
            )()
            for plugin_name in plugins
        ]


def get_landscape_log_directory(landscape_dir=None):
    """
    Work out the correct path to store logs in depending on the effective
    user id of the current process.
    """
    if landscape_dir is None:
        if os.getuid() == 0:
            landscape_dir = "/var/log/landscape"
        else:
            landscape_dir = os.path.expanduser("~/.landscape")
    return landscape_dir


def setup_logging(landscape_dir=None):
    landscape_dir = get_landscape_log_directory(landscape_dir)
    logger = getLogger("landscape-sysinfo")
    logger.propagate = False
    if not os.path.isdir(landscape_dir):
        os.mkdir(landscape_dir)
    log_filename = os.path.join(landscape_dir, "sysinfo.log")
    handler = RotatingFileHandler(
        log_filename,
        maxBytes=500 * 1024,
        backupCount=1,
    )
    logger.addHandler(handler)
    handler.setFormatter(Formatter("%(asctime)s %(levelname)-8s %(message)s"))


def run(args, reactor=None, sysinfo=None):
    """
    @param reactor: The reactor to (optionally) run the sysinfo plugins in.
    """
    try:
        setup_logging()
    except OSError as e:
        sys.exit(f"Unable to setup logging. {e}")

    if sysinfo is None:
        sysinfo = SysInfoPluginRegistry()
    config = SysInfoConfiguration()
    # landscape-sysinfo needs to work where there's no
    # /etc/landscape/client.conf See lp:1293990
    config.load(args, accept_nonexistent_default_config=True)
    for plugin in config.get_plugins():
        sysinfo.add(plugin)

    def show_output(result):
        print(
            format_sysinfo(
                sysinfo.get_headers(),
                sysinfo.get_notes(),
                sysinfo.get_footnotes(),
                width=config.width,
                indent="  ",
            ),
        )

    def run_sysinfo():
        return sysinfo.run().addCallback(show_output)

    if reactor is not None:
        # In case any plugins run processes or do other things that require the
        # reactor to already be started, we delay them until the reactor is
        # running.
        done = Deferred()
        reactor.callWhenRunning(
            lambda: maybeDeferred(run_sysinfo).chainDeferred(done),
        )

        def stop_reactor(result):
            # We won't need to use callLater here once we use Twisted >8.
            # tm:3011
            reactor.callLater(0, reactor.stop)
            return result

        done.addBoth(stop_reactor)
        reactor.run()
    else:
        done = run_sysinfo()
    return done

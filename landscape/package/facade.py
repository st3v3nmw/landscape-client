import hashlib
import os

from smart.transaction import (
    Transaction, PolicyInstall, PolicyUpgrade, PolicyRemove, Failed)
from smart.const import INSTALL, REMOVE, UPGRADE, ALWAYS, NEVER

import smart

import apt
import apt_inst
import apt_pkg
from aptsources.sourceslist import SourcesList

from landscape.lib.fs import append_file, create_file, read_file
from landscape.package.skeleton import build_skeleton, build_skeleton_apt


class TransactionError(Exception):
    """Raised when the transaction fails to run."""


class DependencyError(Exception):
    """Raised when a needed dependency wasn't explicitly marked."""

    def __init__(self, packages):
        self.packages = packages

    def __str__(self):
        return ("Missing dependencies: %s" %
                ", ".join([str(package) for package in self.packages]))


class SmartError(Exception):
    """Raised when Smart fails in an undefined way."""


class ChannelError(Exception):
    """Raised when channels fail to load."""


class AptFacade(object):
    """Wrapper for tasks using Apt.

    This object wraps Apt features, in a way that makes using and testing
    these features slightly more comfortable.

    @param root: The root dir of the Apt configuration files.
    @ivar refetch_package_index: Whether to refetch the package indexes
        when reloading the channels, or reuse the existing local
        database.
    """

    def __init__(self, root=None):
        self._cache = apt.cache.Cache(rootdir=root, memonly=True)
        self._channels_loaded = False
        self._pkg2hash = {}
        self._hash2pkg = {}
        self.refetch_package_index = False

    def get_packages(self):
        """Get all the packages available in the channels."""
        return self._hash2pkg.values()

    def reload_channels(self, debug=False):
        """Reload the channels and update the cache."""
        self._cache.open(None)
        if self.refetch_package_index:
            self._cache.update()
            self._cache.open(None)

        self._pkg2hash.clear()
        self._hash2pkg.clear()
        for package in [self._cache[name] for name in self._cache.keys()]:
            for version in package.versions:
                hash = self.get_package_skeleton(
                    version, with_info=False).get_hash()
                # Use a tuple including the package, since the Version
                # objects of two different packages can have the same
                # hash.
                self._pkg2hash[(package, version)] = hash
                self._hash2pkg[hash] = version

    def ensure_channels_reloaded(self):
        """Reload the channels if they haven't been reloaded yet."""
        if self._channels_loaded:
            return
        self.reload_channels()
        self._channels_loaded = True

    def add_channel_apt_deb(self, url, codename, components=None):
        """Add a deb URL which points to a repository.

        @param url: The base URL of the repository.
        @param codename: The dist in the repository.
        @param components: The components to be included.
        """
        sources_dir = apt_pkg.config.find_dir("Dir::Etc::sourceparts")
        sources_file_path = os.path.join(
            sources_dir, "_landscape-internal-facade.list")
        sources_line = "deb %s %s" % (url, codename)
        if components:
            sources_line += " %s" % " ".join(components)
        sources_line += "\n"
        append_file(sources_file_path, sources_line)

    def add_channel_deb_dir(self, path):
        """Add a directory with packages as a channel.

        @param path: The path to the directory containing the packages.

        A Packages file is created in the directory with information
        about the deb files.
        """
        self._create_packages_file(path)
        self.add_channel_apt_deb("file://%s" % path, "./", None)

    def _create_packages_file(self, deb_dir):
        """Create a Packages file in a directory with debs."""
        packages_contents = "\n".join(
            self.get_package_stanza(os.path.join(deb_dir, filename))
            for filename in sorted(os.listdir(deb_dir)))
        create_file(os.path.join(deb_dir, "Packages"), packages_contents)

    def get_channels(self):
        """Return a list of channels configured.

        A channel is a deb line in sources.list or sources.list.d. It's
        represented by a dict with baseurl, distribution, components,
        and type keys.
        """
        sources_list = SourcesList()
        return [{"baseurl": entry.uri, "distribution": entry.dist,
                 "components": " ".join(entry.comps), "type": entry.type}
                for entry in sources_list if not entry.disabled]

    def reset_channels(self):
        """Remove all the configured channels."""
        sources_list = SourcesList()
        for entry in sources_list:
            entry.set_enabled(False)
        sources_list.save()

    def get_package_stanza(self, deb_path):
        """Return a stanza for the package to be included in a Packages file.

        @param deb_path: The path to the deb package.
        """
        deb_file = open(deb_path)
        deb = apt_inst.DebFile(deb_file)
        control = deb.control.extractdata("control")
        deb_file.close()
        filename = os.path.basename(deb_path)
        size = os.path.getsize(deb_path)
        contents = read_file(deb_path)
        md5 = hashlib.md5(contents).hexdigest()
        sha1 = hashlib.sha1(contents).hexdigest()
        sha256 = hashlib.sha256(contents).hexdigest()
        # Use rewrite_section to ensure that the field order is correct.
        return apt_pkg.rewrite_section(
            apt_pkg.TagSection(control), apt_pkg.REWRITE_PACKAGE_ORDER,
            [("Filename", filename), ("Size", str(size)),
             ("MD5sum", md5), ("SHA1", sha1), ("SHA256", sha256)])

    def get_arch(self):
        """Return the architecture APT is configured to use."""
        return apt_pkg.config.get("APT::Architecture")

    def set_arch(self, architecture):
        """Set the architecture that APT should use."""
        return apt_pkg.config.set("APT::Architecture", architecture)

    def get_package_skeleton(self, pkg, with_info=True):
        """Return a skeleton for the provided package.

        The skeleton represents the basic structure of the package.

        @param pkg: Package to build skeleton from.
        @param with_info: If True, the skeleton will include information
            useful for sending data to the server.  Such information isn't
            necessary if the skeleton will be used to build a hash.

        @return: a L{PackageSkeleton} object.
        """
        return build_skeleton_apt(pkg, with_info=with_info)

    def get_package_hash(self, version):
        """Return a hash from the given package.

        @param version: an L{apt.package.Version} object.
        """
        return self._pkg2hash.get((version.package, version))

    def get_package_hashes(self):
        """Get the hashes of all the packages available in the channels."""
        return self._pkg2hash.values()

    def get_package_by_hash(self, hash):
        """Get the package having the provided hash.

        @param hash: The hash the package should have.

        @return: The L{apt.package.Package} that has the given hash.
        """
        return self._hash2pkg.get(hash)

    def is_package_available(self, version):
        """Is the package available for installation?"""
        return version.downloadable

    def is_package_upgrade(self, version):
        """Is the package an upgrade for another installed package?"""
        if not version.package.is_upgradable or not version.package.installed:
            return False
        return version > version.package.installed

    def get_packages_by_name(self, name):
        """Get all available packages matching the provided name.

        @param name: The name the returned packages should have.
        """
        return [
            version for version in self.get_packages()
            if version.package.name == name]


class SmartFacade(object):
    """Wrapper for tasks using Smart.

    This object wraps Smart features, in a way that makes using and testing
    these features slightly more comfortable.

    @param smart_init_kwargs: A dictionary that can be used to pass specific
        keyword parameters to to L{smart.init}.
    """

    _deb_package_type = None

    def __init__(self, smart_init_kwargs={}, sysconf_args=None):
        self._smart_init_kwargs = smart_init_kwargs.copy()
        self._smart_init_kwargs.setdefault("interface", "landscape")
        self._sysconfig_args = sysconf_args or {}
        self._reset()

    def _reset(self):
        # This attribute is initialized lazily in the _get_ctrl() method.
        self._ctrl = None
        self._pkg2hash = {}
        self._hash2pkg = {}
        self._marks = {}
        self._caching = ALWAYS
        self._channels_reloaded = False

    def deinit(self):
        """Deinitialize the Facade and the Smart library."""
        if self._ctrl:
            smart.deinit()
        self._reset()

    def _get_ctrl(self):
        if self._ctrl is None:
            if self._smart_init_kwargs.get("interface") == "landscape":
                from landscape.package.interface import (
                    install_landscape_interface)
                install_landscape_interface()
            self._ctrl = smart.init(**self._smart_init_kwargs)
            for key, value in self._sysconfig_args.items():
                smart.sysconf.set(key, value, soft=True)
            smart.initDistro(self._ctrl)
            smart.initPlugins()
            smart.sysconf.set("pm-iface-output", True, soft=True)
            smart.sysconf.set("deb-non-interactive", True, soft=True)

            # We can't import it before hand because reaching .deb.* depends
            # on initialization (yeah, sucky).
            from smart.backends.deb.base import DebPackage
            self._deb_package_type = DebPackage

            self.smart_initialized()
        return self._ctrl

    def smart_initialized(self):
        """Hook called when the Smart library is initialized."""

    def ensure_channels_reloaded(self):
        """Reload the channels if they haven't been reloaded yet."""
        if self._channels_reloaded:
            return
        self._channels_reloaded = True
        self.reload_channels()

    def reload_channels(self):
        """
        Reload Smart channels, getting all the cache (packages) in memory.

        @raise: L{ChannelError} if Smart fails to reload the channels.
        """
        ctrl = self._get_ctrl()

        try:
            reload_result = ctrl.reloadChannels(caching=self._caching)
        except smart.Error:
            failed = True
        else:
            # Raise an error only if we are trying to download remote lists
            failed = not reload_result and self._caching == NEVER
        if failed:
            raise ChannelError("Smart failed to reload channels (%s)"
                               % smart.sysconf.get("channels"))

        self._hash2pkg.clear()
        self._pkg2hash.clear()

        for pkg in self.get_packages():
            hash = self.get_package_skeleton(pkg, False).get_hash()
            self._hash2pkg[hash] = pkg
            self._pkg2hash[pkg] = hash

        self.channels_reloaded()

    def channels_reloaded(self):
        """Hook called after Smart channels are reloaded."""

    def get_package_skeleton(self, pkg, with_info=True):
        """Return a skeleton for the provided package.

        The skeleton represents the basic structure of the package.

        @param pkg: Package to build skeleton from.
        @param with_info: If True, the skeleton will include information
            useful for sending data to the server.  Such information isn't
            necessary if the skeleton will be used to build a hash.

        @return: a L{PackageSkeleton} object.
        """
        return build_skeleton(pkg, with_info)

    def get_package_hash(self, pkg):
        """Return a hash from the given package.

        @param pkg: a L{smart.backends.deb.base.DebPackage} objects
        """
        return self._pkg2hash.get(pkg)

    def get_package_hashes(self):
        """Get the hashes of all the packages available in the channels."""
        return self._pkg2hash.values()

    def get_packages(self):
        """
        Get all the packages available in the channels.

        @return: a C{list} of L{smart.backends.deb.base.DebPackage} objects
        """
        return [pkg for pkg in self._get_ctrl().getCache().getPackages()
                if isinstance(pkg, self._deb_package_type)]

    def get_locked_packages(self):
        """Get all packages in the channels matching the set locks."""
        return smart.pkgconf.filterByFlag("lock", self.get_packages())

    def get_packages_by_name(self, name):
        """
        Get all available packages matching the provided name.

        @return: a C{list} of L{smart.backends.deb.base.DebPackage} objects
        """
        return [pkg for pkg in self._get_ctrl().getCache().getPackages(name)
                if isinstance(pkg, self._deb_package_type)]

    def get_package_by_hash(self, hash):
        """
        Get all available packages matching the provided hash.

        @return: a C{list} of L{smart.backends.deb.base.DebPackage} objects
        """
        return self._hash2pkg.get(hash)

    def mark_install(self, pkg):
        self._marks[pkg] = INSTALL

    def mark_remove(self, pkg):
        self._marks[pkg] = REMOVE

    def mark_upgrade(self, pkg):
        self._marks[pkg] = UPGRADE

    def reset_marks(self):
        self._marks.clear()

    def perform_changes(self):
        ctrl = self._get_ctrl()
        cache = ctrl.getCache()

        transaction = Transaction(cache)

        policy = PolicyInstall

        only_remove = True
        for pkg, oper in self._marks.items():
            if oper == UPGRADE:
                policy = PolicyUpgrade
            if oper != REMOVE:
                only_remove = False
            transaction.enqueue(pkg, oper)

        if only_remove:
            policy = PolicyRemove

        transaction.setPolicy(policy)

        try:
            transaction.run()
        except Failed, e:
            raise TransactionError(e.args[0])
        changeset = transaction.getChangeSet()

        if not changeset:
            return None  # Nothing to do.

        missing = []
        for pkg, op in changeset.items():
            if self._marks.get(pkg) != op:
                missing.append(pkg)
        if missing:
            raise DependencyError(missing)

        try:
            self._ctrl.commitChangeSet(changeset)
        except smart.Error, e:
            raise TransactionError(e.args[0])

        output = smart.iface.get_output_for_landscape()
        failed = smart.iface.has_failed_for_landscape()

        smart.iface.reset_for_landscape()

        if failed:
            raise SmartError(output)
        return output

    def reload_cache(self):
        cache = self._get_ctrl().getCache()
        cache.reset()
        cache.load()

    def get_arch(self):
        """
        Get the host dpkg architecture.
        """
        self._get_ctrl()
        from smart.backends.deb.loader import DEBARCH
        return DEBARCH

    def set_arch(self, arch):
        """
        Set the host dpkg architecture.

        To take effect it must be called before L{reload_channels}.

        @param arch: the dpkg architecture to use (e.g. C{"i386"})
        """
        self._get_ctrl()
        smart.sysconf.set("deb-arch", arch)

        # XXX workaround Smart setting DEBARCH statically in the
        # smart.backends.deb.base module
        import smart.backends.deb.loader as loader
        loader.DEBARCH = arch

    def set_caching(self, mode):
        """
        Set Smart's caching mode.

        @param mode: The caching mode to pass to Smart's C{reloadChannels}
            when calling L{reload_channels} (e.g C{smart.const.NEVER} or
            C{smart.const.ALWAYS}).
        """
        self._caching = mode

    def reset_channels(self):
        """Remove all configured Smart channels."""
        self._get_ctrl()
        smart.sysconf.set("channels", {}, soft=True)

    def add_channel(self, alias, channel):
        """
        Add a Smart channel.

        This method can be called more than once to set multiple channels.
        To take effect it must be called before L{reload_channels}.

        @param alias: A string identifying the channel to be added.
        @param channel: A C{dict} holding information about the channel to
            add (see the Smart API for details about valid keys and values).
        """
        channels = self.get_channels()
        channels.update({alias: channel})
        smart.sysconf.set("channels", channels, soft=True)

    def add_channel_apt_deb(self, url, codename, components):
        """Add a Smart channel of type C{"apt-deb"}.

        @see: L{add_channel}
        """
        alias = codename
        channel = {"baseurl": url, "distribution": codename,
                   "components": components, "type": "apt-deb"}
        self.add_channel(alias, channel)

    def add_channel_deb_dir(self, path):
        """Add a Smart channel of type C{"deb-dir"}.

        @see: L{add_channel}
        """
        alias = path
        channel = {"path": path, "type": "deb-dir"}
        self.add_channel(alias, channel)

    def get_channels(self):
        """
        @return: A C{dict} of all configured channels.
        """
        self._get_ctrl()
        return smart.sysconf.get("channels")

    def get_package_locks(self):
        """Return all set package locks.

        @return: A C{list} of ternary tuples, contaning the name, relation
            and version details for each lock currently set on the system.
        """
        self._get_ctrl()
        locks = []
        locks_by_name = smart.pkgconf.getFlagTargets("lock")
        for name in locks_by_name:
            for condition in locks_by_name[name]:
                relation = condition[0] or ""
                version = condition[1] or ""
                locks.append((name, relation, version))
        return locks

    def _validate_lock_condition(self, relation, version):
        if relation and not version:
            raise RuntimeError("Package lock version not provided")
        if version and not relation:
            raise RuntimeError("Package lock relation not provided")

    def set_package_lock(self, name, relation=None, version=None):
        """Set a new package lock.

        Any package matching the given name and possibly the given version
        condition will be locked.

        @param name: The name a package must match in order to be locked.
        @param relation: Optionally, the relation of the version condition the
            package must satisfy in order to be considered as locked.
        @param version: Optionally, the version associated with C{relation}.

        @note: If used at all, the C{relation} and C{version} parameter must be
           both provided.
        """
        self._validate_lock_condition(relation, version)
        self._get_ctrl()
        smart.pkgconf.setFlag("lock", name, relation, version)

    def remove_package_lock(self, name, relation=None, version=None):
        """Remove a package lock."""
        self._validate_lock_condition(relation, version)
        self._get_ctrl()
        smart.pkgconf.clearFlag("lock", name=name, relation=relation,
                                version=version)

    def save_config(self):
        """Flush the current smart configuration to disk."""
        control = self._get_ctrl()
        control.saveSysConf()

    def is_package_available(self, package):
        """Is the package available for installation?"""
        for loader in package.loaders:
            # Is the package also in a non-installed
            # loader?  IOW, "available".
            if not loader.getInstalled():
                return True
        return False

    def is_package_upgrade(self, package):
        """Is the package an upgrade for another installed package?"""
        is_upgrade = False
        for upgrade in package.upgrades:
            for provides in upgrade.providedby:
                for provides_package in provides.packages:
                    if provides_package.installed:
                        is_upgrade = True
                        break
                else:
                    continue
                break
            else:
                continue
            break
        return is_upgrade

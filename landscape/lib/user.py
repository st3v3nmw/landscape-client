import os.path
import pwd


class UnknownUserError(Exception):
    pass


def get_user_info(username=None):
    uid = None
    gid = None
    path = None
    if username is not None:
        try:
            # XXX: We have a situation with the system default FS encoding with
            # Python 3 here: We have to pass a string to pwd.getpwnam(), but if
            # the default does not support unicode characters, a
            # UnicodeEncodeError will be thrown. This edge case can be harmful,
            # if the user was added with a less restrictive encoding active,
            # and is now retrieved with LC_ALL=C for example, as it is during
            # automatic test runs. This should not be a problem under normal
            # circumstances. Alternatively, a different way of parsing
            # /etc/passwd would have to be implemented. A simple
            # locale.setlocale() to use UTF-8 was not successful.
            info = pwd.getpwnam(username)
        except (KeyError, UnicodeEncodeError):
            raise UnknownUserError(f"Unknown user '{username}'")
        uid = info.pw_uid
        gid = info.pw_gid
        path = info.pw_dir
        if not os.path.exists(path):
            path = "/"
    return (uid, gid, path)

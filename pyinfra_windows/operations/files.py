"""
The windows_files module handles windows filesystem state, file uploads and template generation.
"""

import ntpath
import os
from datetime import timedelta

from pyinfra import host, state
from pyinfra.api import FileUploadCommand, OperationError, OperationTypeError, operation
from pyinfra.api.util import get_file_sha1

from pyinfra_windows.facts.server import Date
from pyinfra_windows.facts.files import (
    Directory,
    File,
    Link,
    Md5File,
    Sha1File,
    Sha256File,
)

from .util.files import ensure_mode_int


@operation()
def download(
    src,
    dest,
    user=None,
    group=None,
    mode=None,
    cache_time=None,
    force=False,
    sha256sum=None,
    sha1sum=None,
    md5sum=None,
):
    """
    Download files from remote locations using curl or wget.

    + src: source URL of the file
    + dest: where to save the file
    + user: user to own the files
    + group: group to own the files
    + mode: permissions of the files
    + cache_time: if the file exists already, re-download after this time (in seconds)
    + force: always download the file, even if it already exists
    + sha256sum: sha256 hash to checksum the downloaded file against
    + sha1sum: sha1 hash to checksum the downloaded file against
    + md5sum: md5 hash to checksum the downloaded file against

    **Example:**

    .. code:: python

        windows_files.download(
            name="Download the Docker repo file",
            src="https://download.docker.com/linux/centos/docker-ce.repo",
            dest="C:\\docker",
        )
    """

    info = host.get_fact(File, path=dest)
    # Destination is a directory?
    if info is False:
        raise OperationError(
            "Destination {0} already exists and is not a file".format(dest),
        )

    # Do we download the file? Force by default
    download = force

    # Doesn't exist, lets download it
    if info is None:
        download = True

    # Destination file exists & cache_time: check when the file was last modified,
    # download if old
    else:
        if cache_time:
            # Time on files is not tz-aware, and will be the same tz as the server's time,
            # so we can safely remove the tzinfo from Date before comparison.
            cache_time = host.get_fact(Date).replace(tzinfo=None) - timedelta(
                seconds=cache_time
            )
            if info["mtime"] and info["mtime"] > cache_time:
                download = True

        if sha1sum:
            if sha1sum != host.get_fact(Sha1File, path=dest):
                download = True

        if sha256sum:
            if sha256sum != host.get_fact(Sha256File, path=dest):
                download = True

        if md5sum:
            if md5sum != host.get_fact(Md5File, path=dest):
                download = True

    # If we download, always do user/group/mode as SSH user may be different
    if download:
        yield (
            '$ProgressPreference = "SilentlyContinue"; '
            "Invoke-WebRequest -Uri {0} -OutFile {1}"
        ).format(src, dest)

        # if user or group:
        #    yield chown(dest, user, group)

        # if mode:
        #    yield chmod(dest, mode)

        if sha1sum:
            yield (
                'if ((Get-FileHash -Algorithm SHA1 "{0}").hash -ne {1}) {{ '
                'Write-Error "SHA1 did not match!" '
                "}}"
            ).format(dest, sha1sum)

        if sha256sum:
            yield (
                'if ((Get-FileHash -Algorithm SHA256 "{0}").hash -ne {1}) {{ '
                'Write-Error "SHA256 did not match!" '
                "}}"
            ).format(dest, sha256sum)

        if md5sum:
            yield (
                'if ((Get-FileHash -Algorithm MD5 "{0}").hash -ne {1}) {{ '
                'Write-Error "MD5 did not match!" '
                "}}"
            ).format(dest, md5sum)

    else:
        host.noop("file {0} has already been downloaded".format(dest))


@operation()
def put(
    src,
    dest,
    user=None,
    group=None,
    mode=None,
    add_deploy_dir=True,
    create_remote_dir=True,
    force=False,
    assume_exists=False,
):
    """
    Upload a local file to the remote system.

    + src: local filename to upload
    + dest: remote filename to upload to
    + user: user to own the files
    + group: group to own the files
    + mode: permissions of the files
    + add_deploy_dir: src is relative to the deploy directory
    + create_remote_dir: create the remote directory if it doesn't exist
    + force: always upload the file, even if the remote copy matches
    + assume_exists: whether to assume the local file exists

    ``create_remote_dir``:
        If the remote directory does not exist it will be created using the same
        user & group as passed to ``files.put``. The mode will *not* be copied over,
        if this is required call ``files.directory`` separately.

    Note:
        This operation is not suitable for large files as it may involve copying
        the file before uploading it.

    **Examples:**

    .. code:: python

        # Note: This requires a 'files/motd' file on the local filesystem
        files.put(
            name="Update the message of the day file",
            src="data/content.json",
            dest="C:\\data\\content.json",
        )
    """

    # Upload IO objects as-is
    if hasattr(src, "read"):
        local_file = src

    # Assume string filename
    else:
        # Add deploy directory?
        if add_deploy_dir and state.cwd:
            src = os.path.join(state.cwd, src)

        local_file = src

        if not assume_exists and not os.path.isfile(local_file):
            raise IOError("No such file: {0}".format(local_file))

    mode = ensure_mode_int(mode)
    remote_file = host.get_fact(File, path=dest)

    if create_remote_dir:
        yield from _create_remote_dir(state, host, dest, user, group)

    # No remote file, always upload and user/group/mode if supplied
    if not remote_file or force:
        yield FileUploadCommand(
            local_file,
            dest,
            remote_temp_filename=state.get_temp_filename(dest),
        )

        # if user or group:
        #    yield chown(dest, user, group)

        # if mode:
        #    yield chmod(dest, mode)

    # File exists, check sum and check user/group/mode if supplied
    else:
        local_sum = get_file_sha1(src)
        remote_sum = host.get_fact(Sha1File, path=dest)

        # Check sha1sum, upload if needed
        if local_sum != remote_sum:
            yield FileUploadCommand(
                local_file,
                dest,
                remote_temp_filename=state.get_temp_filename(dest),
            )

            # if user or group:
            #    yield chown(dest, user, group)

            # if mode:
            #    yield chmod(dest, mode)

        else:
            changed = False

            # Check mode
            # if mode and remote_file['mode'] != mode:
            #    yield chmod(dest, mode)
            #    changed = True

            # Check user/group
            # if (
            #    (user and remote_file['user'] != user)
            #    or (group and remote_file['group'] != group)
            # ):
            #    yield chown(dest, user, group)
            #    changed = True

            if not changed:
                host.noop("file {0} is already uploaded".format(dest))


@operation()
def file(
    path,
    present=True,
    assume_present=False,
    user=None,
    group=None,
    mode=None,
    touch=False,
    create_remote_dir=True,
):
    """
    Add/remove/update files.

    + path: path of the remote file
    + present: whether the file should exist
    + assume_present: whether to assume the file exists
    + TODO: user: user to own the files
    + TODO: group: group to own the files
    + TODO: mode: permissions of the files as an integer, eg: 755
    + touch: whether to touch the file
    + create_remote_dir: create the remote directory if it doesn't exist

    ``create_remote_dir``:
        If the remote directory does not exist it will be created using the same
        user & group as passed to ``files.put``. The mode will *not* be copied over,
        if this is required call ``files.directory`` separately.

    **Example:**

    .. code:: python

        files.file(
            name="Create c:\\temp\\hello.txt",
            path="c:\\temp\\hello.txt",
            touch=True,
        )
    """

    if not isinstance(path, str):
        raise OperationTypeError("Name must be a string")

    # mode = ensure_mode_int(mode)
    info = host.get_fact(File, path=path)

    # Not a file?!
    if info is False:
        raise OperationError("{0} exists and is not a file".format(path))

    # Doesn't exist & we want it
    if not assume_present and info is None and present:
        if create_remote_dir:
            yield from _create_remote_dir(state, host, path, user, group)

        yield "New-Item -ItemType file {0}".format(path)

    #        if mode:
    #            yield chmod(path, mode)
    #        if user or group:
    #            yield chown(path, user, group)

    # It exists and we don't want it
    elif (assume_present or info) and not present:
        yield "Remove-Item {0}".format(path)


#    # It exists & we want to ensure its state
#    elif (assume_present or info) and present:
#        if touch:
#            yield 'New-Item -ItemType file {0}'.format(path)
#
#        # Check mode
#        if mode and (not info or info['mode'] != mode):
#            yield chmod(path, mode)
#
#        # Check user/group
#        if (
#            (not info and (user or group))
#            or (user and info['user'] != user)
#            or (group and info['group'] != group)
#        ):
#            yield chown(path, user, group)


def _create_remote_dir(state, host, remote_filename, user, group):
    # Always use POSIX style path as local might be Windows, remote always *nix
    remote_dirname = ntpath.dirname(remote_filename)
    if remote_dirname:
        yield from directory._inner(
            remote_dirname,
            # state=state, # TODO not sure why these args are here
            # host=host,
            user=user,
            group=group,
        )


@operation()
def directory(
    path,
    present=True,
    assume_present=False,
    user=None,
    group=None,
    mode=None,
    recursive=False,
):
    """
    Add/remove/update directories.

    + path: path of the remote folder
    + present: whether the folder should exist
    + assume_present: whether to assume the directory exists
    + TODO: user: user to own the folder
    + TODO: group: group to own the folder
    + TODO: mode: permissions of the folder
    + TODO: recursive: recursively apply user/group/mode

    **Examples:**

    .. code:: python

        files.directory(
            name="Ensure the c:\\temp\\dir_that_we_want_removed is removed",
            path="c:\\temp\\dir_that_we_want_removed",
            present=False,
        )

        files.directory(
            name="Ensure c:\\temp\\foo\\foo_dir exists",
            path="c:\\temp\\foo\\foo_dir",
            recursive=True,
        )

        # multiple directories
        dirs = ["c:\\temp\\foo_dir1", "c:\\temp\\foo_dir2"]
        for dir in dirs:
            files.directory(
                name="Ensure the directory `{}` exists".format(dir),
                path=dir,
            )

    """

    if not isinstance(path, str):
        raise OperationTypeError("Name must be a string")

    info = host.get_fact(Directory, path=path)

    # Not a directory?!
    if info is False:
        raise OperationError("{0} exists and is not a directory".format(path))

    # Doesn't exist & we want it
    if not assume_present and info is None and present:
        yield "New-Item -Path {0} -ItemType Directory".format(path)
        #        if mode:
        #            yield chmod(path, mode, recursive=recursive)
        #        if user or group:
        #            yield chown(path, user, group, recursive=recursive)
        #
        # Somewhat bare fact, should flesh out more

    # It exists and we don't want it
    elif (assume_present or info) and not present:
        # TODO: how to ensure we use 'ps'?
        # remove anything in the directory
        yield "Get-ChildItem {0} -Recurse | Remove-Item".format(path)
        # remove directory
        yield "Remove-Item {0}".format(path)

    # It exists & we want to ensure its state


#    elif (assume_present or info) and present:
#        # Check mode
#        if mode and (not info or info['mode'] != mode):
#            yield chmod(path, mode, recursive=recursive)
#
#        # Check user/group
#        if (
#            (not info and (user or group))
#            or (user and info['user'] != user)
#            or (group and info['group'] != group)
#        ):
#            yield chown(path, user, group, recursive=recursive)


def _validate_path(path):
    try:
        path = os.fspath(path)
    except TypeError:
        raise OperationTypeError("`path` must be a string or `os.PathLike` object")


@operation()
def link(
    path,
    target=None,
    present=True,
    assume_present=False,
    user=None,
    group=None,
    symbolic=True,
    force=True,
    create_remote_dir=True,
):
    """
    Add/remove/update links.

    + path: the name of the link
    + target: the file/directory the link points to
    + present: whether the link should exist
    + assume_present: whether to assume the link exists
    + user: user to own the link
    + group: group to own the link
    + symbolic: whether to make a symbolic link (vs hard link)
    + create_remote_dir: create the remote directory if it doesn't exist

    ``create_remote_dir``:
        If the remote directory does not exist it will be created using the same
        user & group as passed to ``files.put``. The mode will *not* be copied over,
        if this is required call ``files.directory`` separately.

    Source changes:
        If the link exists and points to a different target, pyinfra will remove it and
        recreate a new one pointing to then new target.

    **Examples:**

    .. code:: python

        # simple example showing how to link to a file
        files.link(
            name=r"Create link C:\\issue2 that points to C:\\issue",
            path=r"C:\\issue2",
            target=r"C\\issue",
        )
    """

    _validate_path(path)

    if present and not target:
        raise OperationError("If present is True target must be provided")

    info = host.get_fact(Link, path=path)

    # Not a link?
    if info is not None and not info:
        raise OperationError("{0} exists and is not a link".format(path))

    add_cmd = "New-Item -ItemType {0} -Path {1} -Target {2} {3}".format(
        "SymbolicLink" if symbolic else "HardLink",
        path,
        target,
        "-Force" if force else "",
    )

    remove_cmd = "(Get-Item {0}).Delete()".format(path)

    # We will attempt to link regardless of current existence
    # since we know by now the path is either a link already
    # or does not exist
    if (info is None or force) and present:
        if create_remote_dir:
            yield from _create_remote_dir(state, host, path, user, group)

        yield add_cmd

        # if user or group:
        #    yield chown(path, user, group, dereference=False)

    # It exists and we don't want it
    elif (assume_present or info) and not present:
        yield remove_cmd
    else:
        host.noop("link {0} already exists and force=False".format(path))

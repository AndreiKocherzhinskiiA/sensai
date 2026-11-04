"""Create or update text files under operation-specific permissions and approval."""
from dataclasses import dataclass
import os
import uuid
import hashlib
import re
import stat
from .policy import FilePolicy

from file_access.reader import FileAccessError, WorkspaceReader


@dataclass(frozen=True)
class CreatePlan:
    id: str
    path: str
    content: str
    size_bytes: int


@dataclass(frozen=True)
class WritePlan(CreatePlan):
    expected_sha256: str
    file_identity: tuple


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


def _require_absent(directory_fd, name):
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileAccessError('Target already exists; overwriting is not allowed.')


class WorkspaceWriter(WorkspaceReader):
    """File mutation capability. Creation and updates require separate policy grants.

    At most one preview is pending. Decisions consume it, and neither the path nor
    content can be supplied again at execution time. Approval provenance is the
    caller's responsibility; the CLI accepts it only from the human input prompt.
    """

    def __init__(self, root, policy=None):
        policy = policy if policy is not None else FilePolicy(allow_create=True)
        if not policy.can_create and not policy.can_write:
            raise FileAccessError('File creation is disabled by workspace policy.')
        self._pending = None
        if not {os.link, os.stat, os.unlink} <= os.supports_dir_fd:
            raise FileAccessError('Safe file creation requires Linux/WSL directory-descriptor support.')
        super().__init__(root, policy)

    def close(self):
        self._pending = None
        super().close()

    def _check_root(self):
        if self._root_fd is None:
            raise FileAccessError('Workspace reader is closed.')
        if _identity(os.stat(self.root, follow_symlinks=False)) != _identity(os.fstat(self._root_fd)):
            raise FileAccessError('Workspace changed; restart before saving a file.')

    def prepare_create(self, relative_path, content):
        self._pending = None
        self._authorize('create', relative_path)
        if not isinstance(content, str) or not content or '\0' in content:
            raise FileAccessError('Save content must be nonempty text without NUL characters.')
        try:
            data = content.encode('utf-8')
        except UnicodeError as error:
            raise FileAccessError('Save content must be valid UTF-8 text.') from error
        if len(data) > self.policy.max_bytes:
            raise FileAccessError(f'Answer exceeds the {self.policy.max_bytes}-byte save limit.')
        try:
            self._check_root()
            with self._open_parent(relative_path, 'create') as (directory_fd, name):
                _require_absent(directory_fd, name)
                parent_identity = _identity(os.fstat(directory_fd))
        except OSError as error:
            raise FileAccessError('Cannot prepare save: parent missing, inaccessible or a symbolic link.') from error
        plan = CreatePlan(uuid.uuid4().hex, relative_path, content, len(data))
        self._pending = (plan, parent_identity)
        return plan

    def decide(self, plan_id, approved):
        if self._pending is None or self._pending[0].id != plan_id:
            raise FileAccessError('Save request is unknown, expired or already decided.')
        plan, expected_parent = self._pending
        self._pending = None
        if approved is not True:
            return False
        if isinstance(plan, WritePlan):
            return self._decide_write(plan, expected_parent)
        self._authorize('create', plan.path)
        try:
            self._check_root()
            with self._open_parent(plan.path, 'create') as (directory_fd, name):
                if _identity(os.fstat(directory_fd)) != expected_parent:
                    raise FileAccessError('Destination directory changed; request a new preview.')
                _require_absent(directory_fd, name)
                self._publish(directory_fd, name, plan.content.encode('utf-8'))
        except FileExistsError as error:
            raise FileAccessError('Target appeared during confirmation; it was not overwritten.') from error
        except OSError as error:
            raise FileAccessError('Save failed: destination unavailable or filesystem operation denied.') from error
        return True

    def _version(self, fd):
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise FileAccessError('Only regular, non-hard-linked files may be updated.')
        if before.st_size > self.policy.max_bytes:
            raise FileAccessError('Existing file exceeds the workspace byte limit.')
        os.lseek(fd, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) <= self.policy.max_bytes:
            chunk = os.read(fd, min(4096, self.policy.max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > self.policy.max_bytes or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FileAccessError('File changed while checking its version; read it again.')
        try:
            bytes(data).decode('utf-8')
        except UnicodeError as error:
            raise FileAccessError('Existing file must be UTF-8 text.') from error
        if b'\0' in data:
            raise FileAccessError('Binary content cannot be updated.')
        return _identity(after), hashlib.sha256(data).hexdigest()

    def prepare_write(self, relative_path, content, expected_sha256):
        """Bind replacement content to an existing file and its observed version."""
        self._pending = None
        self._authorize('write', relative_path)
        if not isinstance(content, str) or '\0' in content:
            raise FileAccessError('Replacement must be UTF-8 text without NUL characters.')
        try:
            data = content.encode('utf-8')
        except UnicodeError as error:
            raise FileAccessError('Replacement must be UTF-8 text.') from error
        if len(data) > self.policy.max_bytes:
            raise FileAccessError('Replacement exceeds the workspace byte limit.')
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            raise FileAccessError('Supply the SHA-256 version returned by the latest file read.')
        try:
            self._check_root()
            with self._open_parent(relative_path, 'write') as (directory_fd, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                try:
                    identity, digest = self._version(fd)
                finally:
                    os.close(fd)
                if digest != expected_sha256:
                    raise FileAccessError('File version changed; read it again and request new approval.')
                parent_identity = _identity(os.fstat(directory_fd))
        except OSError as error:
            raise FileAccessError('Cannot prepare update: file missing, inaccessible or a symbolic link.') from error
        plan = WritePlan(uuid.uuid4().hex, relative_path, content, len(data), expected_sha256, identity)
        self._pending = (plan, parent_identity)
        return plan

    def _decide_write(self, plan, expected_parent):
        # Re-check before even opening the file. Consent does not grant permissions.
        self._authorize('write', plan.path)
        import fcntl
        try:
            self._check_root()
            with self._open_parent(plan.path, 'write') as (directory_fd, name):
                if _identity(os.fstat(directory_fd)) != expected_parent:
                    raise FileAccessError('Destination directory changed; request new approval.')
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                try:
                    # Advisory lock serializes cooperating writers. The open descriptor
                    # pins the authorized inode; a path swap cannot redirect the write.
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    identity, digest = self._version(fd)
                    if identity != plan.file_identity or digest != plan.expected_sha256:
                        raise FileAccessError('File changed during approval; read it again.')
                    if _identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != identity:
                        raise FileAccessError('File path changed during approval; read it again.')
                    data = plan.content.encode('utf-8')
                    os.lseek(fd, 0, os.SEEK_SET)
                    position = 0
                    while position < len(data):
                        written = os.write(fd, data[position:])
                        if written <= 0:
                            raise OSError('Incomplete write')
                        position += written
                    os.ftruncate(fd, len(data))
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as error:
            raise FileAccessError('File update failed; inspect the file before retrying. A storage failure may leave partial changes.') from error
        return True

    @staticmethod
    def _publish(directory_fd, name, data):
        # Write privately, then publish by link: complete content, no overwrite.
        temporary = '.sensai-' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                    follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=directory_fd)

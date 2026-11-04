"""Operator-owned file/directory grants; decisions perform no filesystem access."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from structured.output import strict_json


@dataclass(frozen=True)
class FilePolicy:
    extensions: tuple = ('.txt', '.md', '.py', '.json')
    max_bytes: int = 16384
    allow_create: bool = False
    read_paths: tuple = ('.',)
    create_paths: tuple = ('.',)
    write_paths: tuple = ()
    deny_paths: tuple = ()

    def __post_init__(self):
        if (not isinstance(self.extensions, tuple) or not self.extensions or
            any(not isinstance(x, str) or not x.startswith('.') or
                len(x) < 2 or not x[1:].isalnum() or x != x.lower() for x in self.extensions) or
            len(set(self.extensions)) != len(self.extensions)):
            raise ValueError('Policy extensions must be unique lowercase suffixes.')
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1048576:
            raise ValueError('Policy max_bytes must be between 1 and 1048576.')
        if type(self.allow_create) is not bool:
            raise ValueError('Policy allow_create must be a boolean.')
        for rules in (self.read_paths, self.create_paths, self.write_paths, self.deny_paths):
            if not isinstance(rules, tuple) or len(rules) > 128:
                raise ValueError('Permission paths must be a tuple with at most 128 entries.')
            for rule in rules:
                if not isinstance(rule, str) or not rule or len(rule) > 1024:
                    raise ValueError('Permission paths must be nonempty relative paths.')
                if rule == '.':
                    continue
                path = rule[:-1] if rule.endswith('/') else rule
                if (any(ord(c) < 32 or ord(c) == 127 for c in path) or
                    any(c in path for c in ('\\', '*', '?', '[', ']')) or
                    PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive or
                    any(part in ('', '.', '..') for part in path.split('/'))):
                    raise ValueError('Use exact relative file paths, directory/ prefixes, or . for the workspace.')
            if len(set(rules)) != len(rules):
                raise ValueError('Permission paths must be unique.')

    @staticmethod
    def _matches(path, rule):
        return rule == '.' or (path.startswith(rule) if rule.endswith('/') else path == rule)

    def permits(self, operation, relative_path):
        """Deny wins. Permission to read never implies permission to create/write."""
        if not isinstance(relative_path, str) or not relative_path:
            return False
        try:
            relative_path.encode('utf-8')
        except UnicodeError:
            return False
        if (len(relative_path) > 1024 or '\\' in relative_path or
            any(ord(c) < 32 or ord(c) == 127 for c in relative_path) or
            PurePosixPath(relative_path).is_absolute() or PureWindowsPath(relative_path).drive or
            any(part in ('', '.', '..') for part in relative_path.split('/'))):
            return False
        grants = {'read': self.read_paths, 'create': self.create_paths if self.allow_create else (),
                  'write': self.write_paths}.get(operation, ())
        return (not any(self._matches(relative_path, rule) for rule in self.deny_paths)
                and any(self._matches(relative_path, rule) for rule in grants))

    @property
    def can_create(self):
        return self.allow_create and bool(self.create_paths)

    @property
    def can_write(self):
        return bool(self.write_paths)

    @classmethod
    def from_file(cls, path):
        with Path(path).open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('File policy exceeds 64 KiB.')
        data = strict_json(raw.decode('utf-8'))
        required = {'extensions', 'max_bytes', 'allow_create'}
        if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {'permissions'}:
            raise ValueError('File policy requires extensions, max_bytes, allow_create and optional permissions.')
        if not isinstance(data['extensions'], list):
            raise ValueError('Policy extensions must be a list.')
        kwargs = {}
        if 'permissions' in data:
            permissions = data['permissions']
            if not isinstance(permissions, dict) or set(permissions) != {'read', 'create', 'write', 'deny'}:
                raise ValueError('Permissions must explicitly define read, create, write and deny lists.')
            for operation, rules in permissions.items():
                if not isinstance(rules, list):
                    raise ValueError('Each permission must be a list of relative file/directory paths.')
                kwargs[operation + '_paths'] = tuple(rules)
        return cls(tuple(data['extensions']), data['max_bytes'], data['allow_create'], **kwargs)

"""Operator-owned MCP connections; commands and credentials never come from a model."""
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from structured.output import strict_json


class McpError(ValueError):
    """An MCP failure safe to show without server payloads or tracebacks."""


NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,39}\Z')
TOOL_NAME = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str = ''
    args: tuple = ()
    cwd: str = ''
    # None discovers all tools. An empty tuple deliberately disables all tools.
    allow_tools: tuple | None = None
    auto_approve: tuple = ()
    timeout: float = 15
    transport: str = 'stdio'
    env: dict = field(default_factory=dict, repr=False)
    url: str = ''
    headers: dict = field(default_factory=dict, repr=False)


def config_row(config):
    row = {'name': config.name, 'transport': config.transport,
           'auto_approve': list(config.auto_approve), 'timeout': config.timeout}
    if config.allow_tools is not None:
        row['allow_tools'] = list(config.allow_tools)
    if config.transport == 'stdio':
        row.update(command=config.command, args=list(config.args), cwd=config.cwd, env=config.env)
    else:
        row.update(url=config.url, headers=config.headers)
    return row


def load_config(path):
    try:
        path = Path(path).resolve(strict=True)
        with path.open('rb') as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise McpError('MCP configuration exceeds 64 KiB.')
        return parse_config(strict_json(raw.decode('utf-8')), path)
    except McpError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise McpError('Cannot read valid MCP configuration; check the file and paths.') from error


def tool_names(value, label):
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(v, str) or not TOOL_NAME.fullmatch(v) for v in value)
            or len(set(value)) != len(value)):
        raise McpError(label + ' must contain unique MCP tool names (letters, digits, _, - or .).')
    return tuple(value)


def string_map(value, label):
    if (not isinstance(value, dict) or len(value) > 64
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or not k or '\0' in k + v for k, v in value.items())):
        raise McpError(label + ' must be a string-to-string object without NUL characters.')
    return dict(value)


def parse_config(data, path):
    """Accept Sensai's servers list or a conventional mcpServers mapping."""
    path = Path(path)
    try:
        if isinstance(data, dict) and set(data) == {'mcpServers'}:
            mapping = data['mcpServers']
            if not isinstance(mapping, dict) or any(not isinstance(v, dict) or 'name' in v for v in mapping.values()):
                raise McpError('mcpServers must map server names to connection settings.')
            rows = [dict(settings, name=name) for name, settings in mapping.items()]
        elif isinstance(data, dict) and set(data) == {'servers'}:
            rows = data['servers']
        else:
            raise McpError('MCP config must contain a servers list or mcpServers object.')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise McpError('Configure between one and sixteen MCP servers.')
        servers, seen = [], set()
        allowed = {'name', 'command', 'args', 'cwd', 'allow_tools', 'auto_approve',
                   'timeout', 'transport', 'type', 'env', 'url', 'headers'}
        for row in rows:
            if not isinstance(row, dict) or 'name' not in row or set(row) - allowed:
                raise McpError('Invalid MCP server fields.')
            name = row['name']
            if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
                raise McpError('MCP server names must be unique identifiers (up to 40 characters).')
            seen.add(name)
            if 'transport' in row and 'type' in row and row['transport'] != row['type']:
                raise McpError('Conflicting MCP transport settings.')
            transport = row.get('transport', row.get('type', 'http' if 'url' in row else 'stdio'))
            if transport in ('http', 'streamable-http', 'streamable_http'):
                transport = 'http'
            if transport not in ('stdio', 'http'):
                raise McpError('Supported MCP transports: stdio and Streamable HTTP.')
            allow = None if row.get('allow_tools') is None else tool_names(row['allow_tools'], 'allow_tools')
            auto = tool_names(row.get('auto_approve', []), 'auto_approve')
            if allow is not None and not set(auto) <= set(allow):
                raise McpError('auto_approve must be a subset of allow_tools.')
            timeout = row.get('timeout', 15)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
                raise McpError('MCP timeout must be between 0 and 300 seconds.')
            options = dict(name=name, allow_tools=allow, auto_approve=auto, timeout=timeout, transport=transport)
            if transport == 'stdio':
                if row.get('url') or row.get('headers'):
                    raise McpError('Use url and headers only with HTTP connections.')
                command, args = row.get('command'), row.get('args', [])
                if not isinstance(command, str) or not command.strip() or '\0' in command:
                    raise McpError('MCP command must be an executable, not a shell expression.')
                if not isinstance(args, list) or any(not isinstance(a, str) or '\0' in a for a in args):
                    raise McpError('MCP args must be a list of strings.')
                env = string_map(row.get('env', {}), 'env')
                if any('=' in key for key in env):
                    raise McpError('Environment variable names cannot contain =.')
                cwd = row.get('cwd', '.')
                if not isinstance(cwd, str):
                    raise McpError('MCP cwd must be a path relative to the config file.')
                directory = (path.parent / cwd).resolve(strict=True)
                if not directory.is_dir():
                    raise McpError('MCP cwd is not a directory.')
                options.update(command=sys.executable if command == '$PYTHON' else command,
                               args=tuple(args), cwd=str(directory), env=env)
            else:
                if any(row.get(k) for k in ('command', 'args', 'cwd', 'env')):
                    raise McpError('HTTP connections use a URL, not a local executable or environment.')
                url = row.get('url')
                if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
                    raise McpError('MCP HTTP URL must be an absolute http:// or https:// endpoint.')
                parts = urlsplit(url)
                if parts.scheme not in ('http', 'https') or not parts.hostname or parts.fragment or parts.username or parts.password:
                    raise McpError('Use an absolute HTTP URL; put authentication in headers, not the URL.')
                _ = parts.port
                headers = string_map(row.get('headers', {}), 'headers')
                if any(not re.fullmatch(r'[!#$%&\'*+.^_`|~0-9A-Za-z-]+', k)
                       or any(ord(c) < 32 or ord(c) > 126 for c in v) for k, v in headers.items()):
                    raise McpError('Invalid HTTP header name or value.')
                reserved = {'host', 'content-length', 'connection', 'transfer-encoding', 'mcp-session-id', 'mcp-protocol-version'}
                if any(k.lower() in reserved for k in headers):
                    raise McpError('Transport-managed HTTP headers cannot be overridden.')
                options.update(url=url, headers=headers)
            servers.append(ServerConfig(**options))
        return tuple(servers)
    except McpError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise McpError('Cannot read valid MCP configuration; check the file and paths.') from error

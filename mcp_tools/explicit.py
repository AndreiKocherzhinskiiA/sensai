"""Recognize explicit user commands, never model output or retrieved content."""
import json
import re

from structured.output import strict_json
from .config import McpError

NAME = r'[A-Za-z0-9_-]+__[A-Za-z0-9_-]+'
HEADER = re.compile(r'\A\s*(?:Perform exactly one tool call:\s*|Call\s+)(' + NAME + r')\s+(?:Arguments:\s*|with arguments\s*:?[ \t\r\n]*)', re.I)
INLINE = re.compile(r'\A\s*Call\s+(' + NAME + r')\s+with\s+', re.I)
SUFFIX = re.compile(r'\s*\.?\s*(?:(?:Do not read or update any file|Use the create tool specifically)\.)?\s*\Z', re.I)


def parse_explicit_action(text):
    """Parse only a complete top-level command, preserving exact JSON arguments.

    Descriptive text, quotations and previous turns do not become commands.
    Recognized but malformed commands fail closed instead of invoking a planner.
    """
    if not isinstance(text, str):
        return None
    header = HEADER.match(text)
    inline = INLINE.match(text) if not header else None
    if not header and not inline:
        return None
    match = header or inline
    rest = text[match.end():]
    try:
        if header:
            _, end = json.JSONDecoder().raw_decode(rest)
            arguments = strict_json(rest[:end])
            tail = rest[end:]
        else:
            arguments = {}
            tail = rest
            while True:
                key = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)\s+', tail)
                if not key or key[1] in arguments:
                    raise ValueError('Missing or repeated argument')
                value_text = tail[key.end():]
                _, end = json.JSONDecoder().raw_decode(value_text)
                arguments[key[1]] = strict_json(value_text[:end])
                tail = value_text[end:]
                separator = re.match(r'(?:\s*,\s*(?:and\s+)?|\s+and\s+)', tail)
                if not separator:
                    break
                tail = tail[separator.end():]
        if not isinstance(arguments, dict) or not SUFFIX.fullmatch(tail):
            raise ValueError('Expected one argument object and no additional actions')
        return {'name': match[1], 'arguments': arguments}
    except (ValueError, TypeError, RecursionError) as error:
        raise McpError('Invalid explicit tool request. Use: Perform exactly one tool call: server__tool Arguments: {"key":"value"}. No action was executed.') from error

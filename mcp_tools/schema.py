"""Adapt self-contained MCP schemas to the model's structured-output interface."""
from copy import deepcopy
import json

from .config import McpError


def inline_tool_schema(schema):
    """Resolve local JSON pointers, never fetch remote schema documents.

    Model action proposals nest each schema under arguments. Inlining prevents
    a root-relative $ref from accidentally resolving against that wrapper.
    Recursive references are explicitly rejected rather than weakened.
    """
    root = deepcopy(schema)
    budget = 10000

    def visit(value, stack=()):
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise McpError('MCP tool schema expansion exceeds the supported size.')
        if isinstance(value, list):
            return [visit(v, stack) for v in value]
        if not isinstance(value, dict):
            return value
        if '$dynamicRef' in value:
            raise McpError('Dynamic MCP schema references are not supported.')
        if '$ref' in value:
            ref = value['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/') or ref in stack:
                raise McpError('Only non-recursive local MCP schema references are supported.')
            target = root
            try:
                for segment in ref[2:].split('/'):
                    target = target[segment.replace('~1', '/').replace('~0', '~')]
            except (KeyError, TypeError) as error:
                raise McpError('MCP schema reference does not exist.') from error
            resolved = visit(target, stack + (ref,))
            siblings = {k: visit(v, stack) for k, v in value.items() if k != '$ref'}
            return {'allOf': [resolved, siblings]} if siblings else resolved
        return {k: visit(v, stack) for k, v in value.items() if k not in ('$defs', 'definitions')}

    result = visit(root)
    if len(json.dumps(result)) > 32768:
        raise McpError('Expanded MCP tool schema exceeds 32 KiB.')
    return result

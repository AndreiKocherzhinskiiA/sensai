"""MCP initialize/tools/list/tools/call over stdio or Streamable HTTP."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import os
import hashlib
import re
import httpx

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from guardrails import GuardrailError
from structured.output import JsonOutput, StructuredOutputError
from .config import McpError, TOOL_NAME
from .schema import inline_tool_schema


@dataclass
class Tool:
    name: str
    remote_name: str
    server: object
    session: object
    output: JsonOutput
    description: str

    def definition(self):
        return {'type': 'function', 'function': {
            'name': self.name, 'description': self.description,
            'parameters': self.output.schema,
        }}


class Gateway:
    def __init__(self, policy=None):
        self.tools = {}
        self.catalog = {}
        self.server_guidance = {}
        self.policy = policy
        self.failed_servers = set()
        self.protocol_events = []

    def prepare(self, name, arguments):
        tool = self.tools.get(name)
        if tool is None:
            raise McpError('Unknown or disallowed MCP tool.')
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode('utf-8')) > 16384:
                raise McpError('Tool arguments exceed 16 KiB.')
            # Validate both before and after privacy filtering.
            args = tool.output.parse(encoded)
            if self.policy:
                args = self.policy.json_output(self.policy.json_input(args))
                args = tool.output.parse(json.dumps(args, allow_nan=False))
            return args
        except (StructuredOutputError, ValueError, TypeError, RecursionError) as error:
            if isinstance(error, GuardrailError):
                raise
            raise McpError('Tool arguments do not match the discovered schema.') from error

    async def call(self, name, arguments):
        args = self.prepare(name, arguments)
        tool = self.tools[name]
        if tool.server.name in self.failed_servers:
            raise McpError('MCP server unavailable after a failed call; start a new /mcp turn.')
        try:
            result = await asyncio.wait_for(
                tool.session.call_tool(tool.remote_name, arguments=args), tool.server.timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failed_servers.add(tool.server.name)
            raise McpError('MCP call failed or timed out; execution outcome may be unknown.') from error
        self.protocol_events.append({'server': tool.server.name, 'method': 'tools/call',
                                     'tool': tool.remote_name, 'is_error': result.isError})
        # No resource fetching, images, or executable content is handled implicitly.
        if any(block.type != 'text' for block in result.content):
            raise McpError('Only text and structured MCP results are supported.')
        value = {'isError': result.isError,
                 'content': [block.text for block in result.content]}
        if result.structuredContent is not None:
            value['structuredContent'] = result.structuredContent
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode('utf-8')) > 65536:
                raise McpError('MCP result exceeds 64 KiB; result was not released.')
            return self.policy.json_output(self.policy.json_input(value)) if self.policy else value
        except (ValueError, TypeError, RecursionError) as error:
            if isinstance(error, (GuardrailError, McpError)):
                raise
            raise McpError('MCP result is not valid bounded JSON.') from error


@asynccontextmanager
async def connect(servers, policy=None):
    gateway = Gateway(policy)
    try:
        async with AsyncExitStack() as stack:
            # Child stderr is not displayed: it could contain unfiltered data/control codes.
            errors = stack.enter_context(open(os.devnull, 'w'))
            for server in servers:
                if server.transport == 'stdio':
                    params = StdioServerParameters(command=server.command, args=list(server.args),
                                                   cwd=server.cwd, env=server.env or None)
                    read, write = await stack.enter_async_context(stdio_client(params, errlog=errors))
                else:
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        headers=server.headers, timeout=httpx.Timeout(server.timeout, read=300)))
                    read, write, _ = await stack.enter_async_context(
                        streamable_http_client(server.url, http_client=client))
                session = await stack.enter_async_context(ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=server.timeout)))
                initialized = await asyncio.wait_for(session.initialize(), server.timeout)
                guidance = initialized.instructions or ''
                if len(guidance.encode('utf-8')) > 8192:
                    raise McpError('MCP server usage notes exceed 8 KiB.')
                if guidance and policy:
                    guidance = policy.output(policy.check_input(guidance))
                if guidance:
                    gateway.server_guidance[server.name] = guidance
                gateway.protocol_events.append({'server': server.name, 'method': 'initialize',
                    'instructions': guidance,
                    'protocol_version': initialized.protocolVersion,
                    'server_info': initialized.serverInfo.model_dump(mode='json'),
                    'capabilities': initialized.capabilities.model_dump(mode='json', exclude_none=True)})
                cursor, seen_cursors, discovered = None, set(), set()
                for _ in range(8):
                    page = await asyncio.wait_for(session.list_tools(cursor=cursor), server.timeout)
                    gateway.protocol_events.append({'server': server.name, 'method': 'tools/list',
                        'tools': [item.name for item in page.tools]})
                    for item in page.tools:
                        if item.name in discovered or len(discovered) >= 128:
                            raise McpError('Duplicate tools or excessive MCP catalogue.')
                        discovered.add(item.name)
                        if not TOOL_NAME.fullmatch(item.name):
                            raise McpError('Unsupported MCP tool name.')
                        schema = item.inputSchema
                        if len(json.dumps(schema)) > 32768:
                            raise McpError('MCP tool schema exceeds 32 KiB.')
                        output = JsonOutput(inline_tool_schema(schema))
                        description = (item.description or '')[:2000]
                        if policy:
                            policy.check_schema(schema)
                            description = policy.output(policy.check_input(description))
                        name = server.name + '__' + item.name
                        # Ollama/provider function names have tighter constraints than MCP.
                        # Preserve the original name on the wire; use a stable model alias.
                        if len(name) > 64 or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                            suffix = hashlib.sha256(name.encode()).hexdigest()[:12]
                            name = re.sub(r'[^A-Za-z0-9_-]', '_', name)[:51] + '_' + suffix
                        if name in gateway.catalog:
                            raise McpError('Ambiguous MCP tool alias.')
                        tool = Tool(name, item.name, server, session, output, description)
                        gateway.catalog[name] = tool
                        if server.allow_tools is None or item.name in server.allow_tools:
                            gateway.tools[name] = tool
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen_cursors:
                        raise McpError('Repeated MCP pagination cursor.')
                    seen_cursors.add(cursor)
                else:
                    raise McpError('MCP tool catalogue has too many pages.')
                configured = set(server.auto_approve) | set(server.allow_tools or ())
                if not configured <= discovered:
                    raise McpError('Configured MCP tools are missing from server ' + server.name + '.')
            if len(json.dumps([t.definition() for t in gateway.catalog.values()])) > 262144:
                raise McpError('Combined MCP catalogue is too large.')
            yield gateway
    except (McpError, GuardrailError):
        raise
    except Exception as error:
        # AnyIO task groups may wrap an exception from the caller of yield while
        # closing the SDK streams. Preserve safe policy/rejection messages.
        pending = list(getattr(error, 'exceptions', ()))
        while pending:
            nested = pending.pop(0)
            if isinstance(nested, (McpError, GuardrailError)):
                raise nested from None
            pending.extend(getattr(nested, 'exceptions', ()))
        raise McpError('MCP connection failed. Check the server command or URL, credentials, dependencies and configuration.') from error

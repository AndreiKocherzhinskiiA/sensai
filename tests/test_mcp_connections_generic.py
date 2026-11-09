"""Interoperability contracts: no feature-specific adapter, real SDK transports."""
import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from mcp_tools.config import McpError, parse_config
from mcp_tools.runner import tool_loop
from mcp_tools.schema import inline_tool_schema


class GenericConfigTests(unittest.TestCase):
    def test_import_conventional_mapping_without_a_manual_tool_catalogue(self):
        configs = parse_config({'mcpServers': {
            'local-server': {'command':'$PYTHON', 'args':['-m','some_package'], 'env':{'KEY':'value'}},
            'remote': {'type':'http', 'url':'https://example.test/mcp', 'headers':{'Authorization':'Bearer private'}}
        }}, Path.cwd()/'settings.json')
        self.assertEqual(configs[0].command, sys.executable)
        self.assertIsNone(configs[0].allow_tools)
        self.assertEqual(configs[0].auto_approve, ())
        self.assertEqual(configs[1].transport, 'http')
        self.assertNotIn('private', repr(configs[1]))

    def test_invalid_configuration_never_starts_a_connection(self):
        bad = [
            {'name':'x','command':'echo','env':{'X':1}},
            {'name':'x','command':'echo','env':{'X=Y':'bad'}},
            {'name':'x','url':'file:///etc/passwd'},
            {'name':'x','url':'https://user:password@example.test/mcp'},
            {'name':'x','url':'http://localhost/mcp','command':'echo'},
            {'name':'x','url':'http://localhost/mcp','headers':{'Authorization':'x\r\ny'}},
            {'name':'x','url':'http://localhost/mcp','headers':{'Host':'different.test'}},
            {'name':'x','type':'sse','url':'http://localhost/sse'},
            {'name':'x','command':'echo','allow_tools':[],'auto_approve':['ping']},
            {'name':'x','command':'echo','unknown':True},
        ]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(McpError):
                parse_config({'servers':[row]}, Path.cwd()/'settings.json')

    def test_local_schema_references_resolve_without_network_fetches(self):
        schema={'type':'object','properties':{'item':{'$ref':'#/$defs/Item'}},
                '$defs':{'Item':{'type':'string'}}}
        self.assertEqual(inline_tool_schema(schema)['properties']['item'], {'type':'string'})
        for ref in ('https://example.test/schema', '#/$defs/missing'):
            schema['properties']['item']['$ref']=ref
            with self.assertRaises(McpError): inline_tool_schema(schema)
        schema['properties']['item']['$ref']='#/$defs/Item'
        schema['$defs']['Item']={'$ref':'#/$defs/Item'}
        with self.assertRaises(McpError): inline_tool_schema(schema)

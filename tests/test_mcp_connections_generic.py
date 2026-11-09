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
from mcp_tools.manager import ConnectionManager
from mcp_tools.runner import tool_loop
from mcp_tools.schema import inline_tool_schema


FIXTURE = '''import os, sys
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import uvicorn

m = FastMCP('independent-catalogue', json_response=os.getenv('JSON_RESPONSE') == '1')
class Item(BaseModel):
    label: str
    quantity: int

@m.tool(name='inventory.lookup-item')
def lookup(item: Item) -> dict:
    return {'label':item.label, 'quantity':item.quantity, 'marker':os.getenv('MCP_TEST_MARKER', 'http'), 'pid':os.getpid()}

@m.tool()
def ping() -> str:
    return 'independent pong'

if len(sys.argv) == 1:
    m.run()
else:
    app = m.streamable_http_app()
    async def authentication(request, call_next):
        if request.headers.get('authorization') != 'Bearer test-credential':
            return Response(status_code=401)
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=authentication)
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='error')
'''


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


class GenericConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root/'server.py'
        self.fixture.write_text(FIXTURE)
        self.manager = ConnectionManager([], state_path=self.root/'registry.json')
        self.addAsyncCleanup(self.manager.close)

    def stdio(self, name='unfamiliar'):
        return {'name':name, 'command':sys.executable, 'args':[str(self.fixture)],
                'env':{'MCP_TEST_MARKER':'passed-through-env'}, 'timeout':5}

    async def test_discovery_env_schema_alias_approval_model_continuation_and_policy(self):
        await self.manager.add(self.stdio())
        async with self.manager.session() as view:
            tool=next(t for t in view.tools.values() if t.remote_name=='inventory.lookup-item')
            self.assertLessEqual(len(tool.name),64)
            self.assertNotIn('.',tool.name)
            self.assertNotIn('$ref',json.dumps(tool.output.schema))
            proposed={'item':{'label':'cube','quantity':2}}
            model=AsyncMock(side_effect=[{'role':'assistant','content':'','tool_calls':[
                {'function':{'name':tool.name,'arguments':proposed}}]},
                {'role':'assistant','content':'Two cubes were looked up.'}])
            approve=AsyncMock(return_value=True)
            turn=await tool_loop(view,[{'role':'user','content':'Look up two cubes.'}],
                                'http://unused','model',5,request=model,approve=approve)
            approve.assert_awaited_once()
            self.assertIn('passed-through-env',json.dumps(turn.evidence))
            self.assertIn('passed-through-env',str(model.call_args_list[1]))
            self.assertEqual(turn.answer,'Two cubes were looked up.')
            with self.assertRaises(McpError): await view.call(tool.name,{'item':{'label':'cube','quantity':'invalid'}})
            info=self.manager.snapshots()[0]
            self.assertEqual(len(info['tools']),2)
            self.assertTrue(all(t['approval']=='ask' for t in info['tools']))
            self.assertNotIn('passed-through-env',json.dumps(info))
            await self.manager.set_tools('unfamiliar',{'allow_tools':['ping'],'auto_approve':[]})
            with self.assertRaises(McpError): await view.call(tool.name,proposed)
        async with self.manager.session() as view:
            self.assertEqual(list(view.tools),['unfamiliar__ping'])
            self.assertIn('independent pong',json.dumps(await view.call('unfamiliar__ping',{})))
        saved=self.manager.snapshots()[0]
        self.assertEqual(len(saved['tools']),2) # excluded tools remain selectable
        await self.manager.set_tools('unfamiliar',{'allow_tools':[],'auto_approve':[]})
        async with self.manager.session() as view:self.assertFalse(view.tools)
        restored=ConnectionManager([],state_path=self.manager.state_path)
        self.addAsyncCleanup(restored.close)
        async with restored.session() as view:self.assertFalse(view.tools)
        self.assertEqual(restored.connections['unfamiliar'].config.env['MCP_TEST_MARKER'],'passed-through-env')
        self.assertEqual(self.manager.state_path.stat().st_mode & 0o777,0o600)

    async def test_import_is_atomic_and_two_servers_do_not_share_names(self):
        rows=[self.stdio('one'),self.stdio('two')]
        await self.manager.add({'mcpServers':{row['name']:{k:v for k,v in row.items() if k!='name'} for row in rows}})
        with self.assertRaises(McpError):
            await self.manager.add({'servers':[self.stdio('three'),self.stdio('one')]})
        self.assertNotIn('three',self.manager.connections)
        async with self.manager.session() as view:
            self.assertIn('one__ping',view.tools)
            self.assertIn('two__ping',view.tools)
        await self.manager.control('one','disable')
        async with self.manager.session() as view:
            self.assertNotIn('one__ping',view.tools)
            self.assertIn('two__ping',view.tools)

    @unittest.skipUnless(importlib.util.find_spec('mcp_server_time'),'Install external Time server')
    async def test_preexisting_time_server_without_special_configuration(self):
        await self.manager.add({'mcpServers':{'third-party':{
            'command':sys.executable,'args':['-m','mcp_server_time']}}})
        async with self.manager.session() as view:
            self.assertEqual(len(view.tools),2)
            result=await view.call('third-party__get_current_time',{'timezone':'Europe/Madrid'})
            self.assertFalse(result['isError'])
            self.assertIn('Europe/Madrid',json.dumps(result))

    async def test_real_streamable_http_json_and_sse_responses_lifecycle_and_auth(self):
        for json_response in (False,True):
            with self.subTest(json_response=json_response):
                sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
                import os
                env=dict(os.environ,JSON_RESPONSE='1' if json_response else '0')
                process=subprocess.Popen([sys.executable,str(self.fixture),str(port)],env=env,
                                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    url=f'http://127.0.0.1:{port}/mcp'
                    async with httpx.AsyncClient() as client:
                        for _ in range(100):
                            try:
                                response=await client.get(url,timeout=.2)
                                if response.status_code==401:break
                            except httpx.HTTPError:pass
                            await asyncio.sleep(.05)
                        else:self.fail('Independent HTTP fixture did not start')
                    name='http_json' if json_response else 'http_sse'
                    await self.manager.add({'name':name,'url':url,'headers':{'Authorization':'Bearer test-credential'},'timeout':3})
                    await self.manager.control(name,'connect')
                    async with self.manager.session() as view:
                        self.assertIn('independent pong',json.dumps(await view.call(name+'__ping',{})))
                        stale=view
                    info=next(s for s in self.manager.snapshots() if s['name']==name)
                    self.assertEqual(info['transport'],'http')
                    self.assertNotIn('test-credential',json.dumps(info))
                    await self.manager.control(name,'restart')
                    with self.assertRaises(McpError):await stale.call(name+'__ping',{})
                    await self.manager.control(name,'close')
                    self.assertIsNone(process.poll()) # remote server is independently owned
                    async with self.manager.session() as view:self.assertNotIn(name+'__ping',view.tools)
                    await self.manager.add({'name':name+'_bad_auth','url':url,'timeout':1})
                    await self.manager.discover()
                    self.assertEqual(self.manager.connections[name+'_bad_auth'].state,'failed')
                    await self.manager.control(name,'connect')
                    async with self.manager.session() as view:
                        self.assertIn(name+'__ping',view.tools)
                    await self.manager.control(name,'remove')
                finally:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

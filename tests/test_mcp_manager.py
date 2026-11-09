import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from mcp_tools.config import load_config, McpError
from mcp_tools.manager import ConnectionManager

ROOT=Path(__file__).resolve().parents[1]
def payload(result):
    return result.get('structuredContent') or json.loads(result['content'][0])

class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        fixture=root/'server.py'
        fixture.write_text('''import os, asyncio
from mcp.server.fastmcp import FastMCP
m=FastMCP('independent-fixture')
count=0
@m.tool()
async def count_calls(delay: float=0) -> dict:
    global count
    await asyncio.sleep(delay)
    count+=1
    return {'count':count,'pid':os.getpid()}
m.run()
''')
        config=replace(load_config(ROOT/'configs/mcp.demo.json')[0],name='fixture',
            args=(str(fixture),),allow_tools=('count_calls',),timeout=3)
        self.manager=ConnectionManager([config],state_path=root/'registry.json')
        self.addAsyncCleanup(self.manager.close)

    async def test_session_reused_restart_replaces_process_close_does_not_reconnect(self):
        async with self.manager.session() as one:
            a=await one.call('fixture__count_calls',{})
        async with self.manager.session() as two:
            b=await two.call('fixture__count_calls',{})
        self.assertEqual(payload(a)['pid'],payload(b)['pid'])
        self.assertEqual(payload(b)['count'],2)
        await self.manager.control('fixture','restart')
        with self.assertRaises(McpError):await one.call('fixture__count_calls',{})
        async with self.manager.session() as three:
            c=await three.call('fixture__count_calls',{})
        self.assertNotEqual(payload(c)['pid'],payload(a)['pid'])
        await self.manager.control('fixture','close')
        async with self.manager.session() as view:self.assertEqual(view.tools,{})
        with self.assertRaises(ProcessLookupError):os.kill(payload(c)['pid'],0)
        await self.manager.control('fixture','connect')
        self.assertEqual(self.manager.snapshots()[0]['status'],'connected')

    async def test_disable_persists_and_invalidates_pending_approval(self):
        async with self.manager.session() as view:
            view.prepare('fixture__count_calls',{})
            await self.manager.control('fixture','disable')
            with self.assertRaises(McpError):await view.call('fixture__count_calls',{})
        restored=ConnectionManager([],state_path=self.manager.state_path)
        self.addAsyncCleanup(restored.close)
        async with restored.session() as view:self.assertEqual(view.tools,{})
        self.assertEqual(restored.snapshots()[0]['status'],'disabled')
        await restored.control('fixture','enable')
        self.assertEqual(restored.snapshots()[0]['status'],'connected')

    async def test_cancel_call_keeps_session_usable_and_close_interrupts_call(self):
        async with self.manager.session() as view:
            task=asyncio.create_task(view.call('fixture__count_calls',{'delay':2}))
            await asyncio.sleep(.1);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            result=await asyncio.wait_for(view.call('fixture__count_calls',{}),2)
            self.assertFalse(result['isError'])
            task=asyncio.create_task(view.call('fixture__count_calls',{'delay':2}))
            await asyncio.sleep(.1);await self.manager.control('fixture','close')
            with self.assertRaises(McpError):await task

    async def test_real_third_party_added_and_broken_server_isolated(self):
        time=load_config(ROOT/'configs/mcp.demo.json')[1]
        from dataclasses import asdict
        row=json.loads(json.dumps(asdict(time)))
        row['name']='external_time'
        await self.manager.add(row)
        bad={**row,'name':'broken','command':'/nonexistent-mcp-command'}
        await self.manager.add(bad)
        async with self.manager.session() as view:
            result=await view.call('external_time__get_current_time',{'timezone':'Europe/Madrid'})
            self.assertIn('Europe/Madrid',json.dumps(result));self.assertFalse(result['isError'])
            self.assertIn('fixture__count_calls',view.tools)
        info={s['name']:s for s in self.manager.snapshots()}
        self.assertEqual(info['broken']['status'],'failed')
        self.assertEqual(info['external_time']['server_info']['name'],'mcp-time')
        self.assertTrue(info['external_time']['protocol_version'])
        self.assertIn('inputSchema',info['external_time']['tools'][0])
        await self.manager.control('external_time','remove')
        self.assertNotIn('external_time',self.manager.connections)

    async def test_concurrent_requests_are_serialized(self):
        async with self.manager.session() as view:
            results=await asyncio.gather(*(view.call('fixture__count_calls',{}) for _ in range(5)))
        self.assertEqual([payload(r)['count'] for r in results],list(range(1,6)))

    async def test_transport_timeout_requires_restart_and_other_connection_survives(self):
        async with self.manager.session() as view:
            with self.assertRaises(McpError):await view.call('fixture__count_calls',{'delay':5})
        await asyncio.sleep(.1)
        self.assertEqual(self.manager.snapshots()[0]['status'],'failed')
        await self.manager.control('fixture','restart')
        async with self.manager.session() as view:
            self.assertFalse((await view.call('fixture__count_calls',{}))['isError'])

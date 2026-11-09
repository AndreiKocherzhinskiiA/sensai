"""Regression checks for MCP capability notes and model context boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from mcp_tools.config import McpError, parse_config
from mcp_tools.manager import ConnectionManager
from mcp_tools.runner import request_message, plan_workflow, tool_loop


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_receives_explicit_configurable_context_and_output_budget(self):
        real_client=httpx.AsyncClient
        sent=[]
        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'done':True,'prompt_eval_count':5000,
                'message':{'role':'assistant','content':'ok'}})
        with patch.dict('os.environ',{'SENSAI_MCP_NUM_CTX':'16384'}), \
             patch('mcp_tools.runner.httpx.AsyncClient',side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(respond),**kw)):
            self.assertEqual((await request_message('http://test','model',[],[],2))['content'],'ok')
        self.assertEqual(sent[0]['options']['num_ctx'],16384)
        self.assertEqual(sent[0]['options']['num_predict'],2048)
        with patch.dict('os.environ',{'SENSAI_MCP_NUM_CTX':'32768'}), \
             patch('mcp_tools.runner.httpx.AsyncClient',side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(respond),**kw)):
            await request_message('http://test','model',[],[],2)
        self.assertEqual(sent[-1]['options']['num_ctx'],32768)

    async def test_saturated_context_cannot_release_a_model_action(self):
        real_client=httpx.AsyncClient
        response={'done':True,'prompt_eval_count':16000,'message':{
            'role':'assistant','content':'','tool_calls':[{'function':{'name':'app__change','arguments':{}}}]}}
        with patch.dict('os.environ',{'SENSAI_MCP_NUM_CTX':'16384'}), \
             patch('mcp_tools.runner.httpx.AsyncClient',side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response)),**kw)):
            with self.assertRaisesRegex(McpError,'context is nearly full'):
                await request_message('http://test','model',[],[],2)
        for invalid in ('NaN','1','999999'):
            with self.subTest(value=invalid), patch.dict('os.environ',{'SENSAI_MCP_NUM_CTX':invalid}), \
                 patch('mcp_tools.runner.httpx.AsyncClient') as client:
                with self.assertRaises(McpError): await request_message('http://test','model',[],[],2)
                client.assert_not_called()


    async def test_qwen_nonthinking_prefill_is_wire_only_and_preserves_user_history(self):
        from mcp_tools.runner import model_messages
        original=[{'role':'user','content':'Use the connected tool.'}]
        with patch.dict('os.environ',{'SENSAI_MCP_NO_THINK_PREFIX':'auto'}):
            prepared=model_messages('qwen3:4b',original,False)
            self.assertEqual(prepared[-1],{'role':'assistant','content':'<think>\n\n</think>\n\n'})
            self.assertEqual(original,[{'role':'user','content':'Use the connected tool.'}])
            for name,think in [('llama3.2:1b',False),('qwen3:4b-instruct',False),('qwen3:4b',True),('qwen3:4b',None)]:
                with self.subTest(model=name,think=think):
                    self.assertEqual(model_messages(name,original,think),original)
        with patch.dict('os.environ',{'SENSAI_MCP_NO_THINK_PREFIX':'off'}):
            self.assertEqual(model_messages('qwen3:4b',original,False),original)
        with patch.dict('os.environ',{'SENSAI_MCP_NO_THINK_PREFIX':'on'}):
            self.assertEqual(model_messages('local-alias',original,False)[-1]['role'],'assistant')

    async def test_qwen_structured_request_uses_closed_thinking_prefix(self):
        real_client=httpx.AsyncClient
        sent=[]
        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'done':True,'message':{'role':'assistant','content':'{"ok":true}'}})
        with patch.dict('os.environ',{'SENSAI_MCP_NO_THINK_PREFIX':'auto'}), \
             patch('mcp_tools.runner.httpx.AsyncClient',side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(respond),**kw)):
            await request_message('http://test','qwen3:4b',[{'role':'user','content':'Return JSON'}],[],2,{'type':'object'})
        self.assertFalse(sent[0]['think'])
        self.assertEqual(sent[0]['messages'][-1]['content'],'<think>\n\n</think>\n\n')

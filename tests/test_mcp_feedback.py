"""Real MCP process with application errors inside successful wire envelopes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from mcp_tools.config import McpError,parse_config
from mcp_tools.manager import ConnectionManager
from mcp_tools.runner import run_optional_turn,request_action


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        source='''from mcp.server.fastmcp import FastMCP
m=FastMCP('external-editor')
value=0
attempts=0
@m.tool()
def execute(code:str)->str:
    global value,attempts
    attempts+=1
    if code=='broken':return 'Error executing code: AttributeError: old API does not exist'
    value=2
    return 'Updated value to 2'
@m.tool()
def inspect_state(field:str='value')->dict:
    if field!='value':return {'error':'AttributeError: unknown field; use value'}
    return {'value':value,'attempts':attempts}
m.run()
'''
        self.config=parse_config({'mcpServers':{'editor':{'command':'$PYTHON','args':['-c',source]}}},Path(self.temp.name)/'config.json')
        self.manager=ConnectionManager(self.config);self.addAsyncCleanup(self.manager.close)
        self.plan=[{'tool':'editor__execute','goal':'Set value to 2','condition':'always'},
                   {'tool':'editor__inspect_state','goal':'Verify value','condition':'always'},
                   {'tool':'editor__execute','goal':'Correct only if needed','condition':'if_needed'},
                   {'tool':'editor__inspect_state','goal':'Verify correction','condition':'if_needed'}]
        self.events=[];self.approvals=[];self.prompts=[]

    @staticmethod
    def action(code=None):
        return {'role':'assistant','content':'','tool_calls':[{'function':{
            'name':'editor__inspect_state' if code is None else 'editor__execute',
            'arguments':{} if code is None else {'code':code}}}]}

    async def model(self,host,model,messages,tools,timeout,schema):
        payload=json.loads(messages[1]['content']);self.prompts.append(payload)
        if 'observation' in schema['properties']:
            failed='AttributeError' in json.dumps(payload['actual_observation'])
            value={'status':'failed' if failed else 'succeeded',
                   'operation':'read' if payload['actual_observation']['tool']=='editor__inspect_state' else 'change',
                   'observation':str(payload['actual_observation']['result']) if failed else 'Requested operation succeeded'}
        elif 'checks' in schema['properties']:
            complete=payload['actual_observations'][-1]['tool']=='editor__inspect_state'
            value={'checks':[{'requirement':'value equals 2','observed':'2' if complete else 'operation failed','met':complete}],
                   'answer':'Actual value is 2.' if complete else 'The update failed.'}
        else:
            value={'steps':[{'tool':'editor__execute','goal':'Correct the failing API and set value to 2','purpose':'change'},
                            {'tool':'editor__inspect_state','goal':'Read actual value','purpose':'inspect'}],
                   'reason':'Correct the application error, then verify.'}
        return {'role':'assistant','content':json.dumps(value)}

    async def run_check(self,proposals,approval=None):
        def consent(name,args):
            self.approvals.append((name,args))
            return True if approval is None else approval(name,args)
        with patch('mcp_tools.runner.plan_workflow',AsyncMock(return_value=list(self.plan))), \
             patch('mcp_tools.runner.request_action',AsyncMock(side_effect=proposals)) as generate, \
             patch('mcp_tools.runner.request_message',side_effect=self.model):
            result=await run_optional_turn(self.config,[{'role':'user','content':'Set value to 2; verify and correct if needed.'}],
                'http://test','model',5,manager=self.manager,approve=consent,
                notify=lambda k,p:self.events.append((k,p)))
        return result,generate

    async def state(self):
        async with self.manager.session() as gateway:
            result=await gateway.call('editor__inspect_state',{})
        return result.get('structuredContent') or json.loads(result['content'][0])

    async def test_payload_error_replans_before_dependent_steps_and_preserves_wire_evidence(self):
        result,generate=await self.run_check([self.action('broken'),self.action('corrected'),self.action()])
        self.assertFalse(result.evidence[0]['result']['isError'])
        self.assertIn('AttributeError',str(result.evidence[0]['result']))
        self.assertEqual([e['tool'] for e in result.evidence],['editor__execute','editor__execute','editor__inspect_state'])
        self.assertEqual(await self.state(),{'value':2,'attempts':2})
        self.assertEqual(len(self.approvals),3)
        corrective_context=generate.call_args_list[1].kwargs['step_context']
        self.assertEqual(corrective_context['current_step']['purpose'],'change')
        self.assertEqual(corrective_context['execution_problem']['status'],'failed')
        self.assertIn('AttributeError',str(corrective_context['actual_observations']))

    async def test_unchanged_failed_program_is_not_approved_or_executed_again(self):
        with self.assertRaisesRegex(McpError,'unchanged'):
            await self.run_check([self.action('broken'),self.action('broken')])
        self.assertEqual(await self.state(),{'value':0,'attempts':1})
        self.assertEqual(len(self.approvals),1)

    async def test_success_skips_conditional_repair_and_its_inspection(self):
        result,generate=await self.run_check([self.action('corrected'),self.action()])
        self.assertEqual(result.answer,'Actual value is 2.')
        self.assertEqual(generate.await_count,2)
        self.assertEqual(await self.state(),{'value':2,'attempts':1})
        self.assertEqual(len(self.approvals),2)

    async def test_rejected_repair_leaves_state_unchanged(self):
        with self.assertRaisesRegex(McpError,'rejected'):
            await self.run_check([self.action('broken'),self.action('corrected')],
                approval=lambda name,args:args.get('code')!='corrected')
        self.assertEqual(await self.state(),{'value':0,'attempts':1})

    async def test_failed_read_only_retries_read_without_repeating_the_mutation(self):
        bad_read=self.action()
        bad_read['tool_calls'][0]['function']['arguments']={'field':'wrong'}
        result,generate=await self.run_check([self.action('corrected'),bad_read,self.action()])
        self.assertEqual([e['tool'] for e in result.evidence],
            ['editor__execute','editor__inspect_state','editor__inspect_state'])
        self.assertEqual(await self.state(),{'value':2,'attempts':1})
        retry_context=generate.call_args_list[2].kwargs['step_context']
        self.assertEqual(retry_context['current_step']['purpose'],'inspect')
        self.assertIn('unknown field',retry_context['current_step']['goal'])

    async def test_selected_step_generates_arguments_from_the_discovered_schema(self):
        async with self.manager.session() as gateway:
            definition=gateway.tools['editor__execute'].definition()
        model=AsyncMock(return_value={'role':'assistant','content':'{"code":"corrected"}'})
        with patch('mcp_tools.runner.request_message',model):
            proposal=await request_action('','',[{'role':'user','content':'Correct the failed operation'}],
                [definition],5,selected_step=True)
        self.assertEqual(proposal['tool_calls'][0]['function'],
            {'name':'editor__execute','arguments':{'code':'corrected'}})
        self.assertEqual(model.call_args.args[5],definition['function']['parameters'])

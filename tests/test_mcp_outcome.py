import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import httpx

from mcp_tools.config import McpError,parse_config
from mcp_tools.manager import ConnectionManager
from mcp_tools.runner import run_optional_turn,review_workflow,request_message

def outcome(status='complete',met=True,steps=None,answer='Observed value is 2.'):
    return {'status':status,'checks':[{'requirement':'value equals 2','observed':'2' if met else '1','met':met}],
            'steps':steps or [],'answer':'' if status=='repair' else answer}

def decoded_result(result):
    return result.get('structuredContent') or json.loads(result['content'][0])

def action(tool,args):
    return {'role':'assistant','content':'','tool_calls':[{'function':{'name':tool,'arguments':args}}]}

class OutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        execution=patch('mcp_tools.runner.review_execution',AsyncMock(return_value={
            'status':'succeeded','observation':'Controlled operation result'}))
        execution.start();self.addCleanup(execution.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        source='''from mcp.server.fastmcp import FastMCP
m=FastMCP('independent-stateful-app')
value=0
@m.tool()
def set_value(new_value:float)->dict:
    global value
    value=new_value
    return {'value':value}
@m.tool()
def inspect_value()->dict:
    return {'value':value}
m.run()
'''
        self.configs=parse_config({'mcpServers':{'app':{'command':'$PYTHON','args':['-c',source]}}},root/'settings.json')
        self.manager=ConnectionManager(self.configs);self.addAsyncCleanup(self.manager.close)
        self.initial=[{'tool':'app__set_value','goal':'Set value to 2'},
                      {'tool':'app__inspect_value','goal':'Verify actual value'}]
        self.correction=[{'tool':'app__set_value','goal':'Observed 1, required 2: update existing value to 2','purpose':'change'},
                         {'tool':'app__inspect_value','goal':'Verify corrected value','purpose':'inspect'}]
        self.proposals=[action('app__set_value',{'new_value':1}),action('app__inspect_value',{}),
                        action('app__set_value',{'new_value':2}),action('app__inspect_value',{})]

    async def run_check(self,reviews,approve=lambda *_:True,max_calls=4):
        self.trace=[]
        responses=[]
        for review in reviews:
            responses.append({'checks':review['checks'],'answer':review['answer'] or 'Observed value is 1; required 2.'})
            if review['status']=='repair':
                responses.append({'steps':review['steps'],'reason':'Correct the observed mismatch and verify.'})
        model=AsyncMock(side_effect=[{'role':'assistant','content':json.dumps(r)} for r in responses])
        with patch('mcp_tools.runner.plan_workflow',AsyncMock(return_value=list(self.initial))), \
             patch('mcp_tools.runner.request_action',AsyncMock(side_effect=self.proposals)), \
             patch('mcp_tools.runner.request_message',model):
            result=await run_optional_turn(self.configs,[{'role':'user','content':'Set value to 2 and verify it.'}],
                'http://test','model',5,manager=self.manager,approve=approve,max_calls=max_calls,
                notify=lambda k,p:self.trace.append((k,p)))
        return result,model

    async def test_successful_call_with_wrong_result_gets_approved_repair_and_fresh_check(self):
        approvals=[]
        result,model=await self.run_check([outcome('repair',False,self.correction),outcome()],
            approve=lambda n,a:approvals.append((n,a)) or True)
        self.assertEqual(len(approvals),4)
        self.assertEqual([decoded_result(e['result'])['value'] for e in result.evidence],[1,1,2,2])
        self.assertEqual(result.answer,'Observed value is 2.')
        self.assertEqual([p['status'] for k,p in self.trace if k=='review'],['repair','complete'])
        payload=json.loads(model.call_args_list[0].args[2][1]['content'])
        self.assertEqual(payload['actual_observations'][0]['arguments'],{'new_value':1})
        self.assertEqual(payload['remaining_calls'],2)
        final_payload=json.loads(model.call_args_list[-1].args[2][1]['content'])
        self.assertEqual(final_payload['remaining_calls'],0)

    async def test_rejected_correction_cannot_mutate_state_or_release_success(self):
        with self.assertRaisesRegex(McpError,'rejected'):
            await self.run_check([outcome('repair',False,self.correction)],
                approve=lambda n,a:a.get('new_value')!=2)
        async with self.manager.session() as view:
            actual=await view.call('app__inspect_value',{})
        self.assertEqual(decoded_result(actual)['value'],1)
        self.assertEqual(sum(k=='call' for k,_ in self.trace),2)

    async def test_exhausted_budget_reports_incomplete_without_more_actions(self):
        with self.assertRaisesRegex(McpError,'not verified'):
            await self.run_check([outcome('incomplete',False,answer='Actual value is 1, not the requested 2.')],max_calls=2)
        self.assertEqual(sum(k=='call' for k,_ in self.trace),2)

    async def test_invalid_assessment_never_releases_answer_or_action(self):
        async with self.manager.session() as view:definitions=[t.definition() for t in view.tools.values()]
        variants=[{'checks':[],'answer':'Claimed success'},
                  {'checks':outcome()['checks'],'answer':'x'*1201},
                  {'checks':outcome()['checks'],'answer':'Success','status':'complete'},
                  {'checks':outcome()['checks'],'answer':'   '}]
        for value in variants:
            with self.subTest(value=value),patch('mcp_tools.runner.request_message',AsyncMock(return_value={'content':json.dumps(value)})):
                with self.assertRaises(McpError):
                    await review_workflow('','', 'Set value to 2',definitions,[],5,2)
        with patch('mcp_tools.runner.request_message',AsyncMock(return_value={'content':'Several paragraphs of deliberation...'})):
            with self.assertRaises(McpError):await review_workflow('','','Set value to 2',definitions,[],5,2)

    async def test_mutation_without_independent_inspection_is_rejected(self):
        async with self.manager.session() as view:definitions=[t.definition() for t in view.tools.values()]
        assessment={'checks':outcome(met=False)['checks'],'answer':'Actual 1, required 2.'}
        for steps in ([self.correction[0]],list(reversed(self.correction))):
            with self.subTest(steps=steps),patch('mcp_tools.runner.request_message',AsyncMock(side_effect=[
                {'content':json.dumps(assessment)},
                {'content':json.dumps({'steps':steps,'reason':'Change value'})}])):
                with self.assertRaisesRegex(McpError,'separate inspection'):
                    await review_workflow('','','Set value to 2',definitions,[],5,2)

    async def test_one_remaining_call_cannot_authorize_a_mutation(self):
        async with self.manager.session() as view:definitions=[t.definition() for t in view.tools.values()]
        assessment={'checks':outcome(met=False)['checks'],'answer':'Actual 1, required 2.'}
        bad={'steps':[self.correction[0]],'reason':'Change value'}
        with patch('mcp_tools.runner.request_message',AsyncMock(side_effect=[
            {'content':json.dumps(r)} for r in (assessment,bad,bad)])):
            with self.assertRaises(McpError):await review_workflow('','','Set value to 2',definitions,[],5,1)

    async def test_truncated_model_content_is_not_released(self):
        real_client=httpx.AsyncClient
        response={'done':True,'done_reason':'length','message':{'role':'assistant','content':'Unfinished reasoning...'}}
        with patch('mcp_tools.runner.httpx.AsyncClient',side_effect=lambda **kw:
                   real_client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response)),**kw)):
            with self.assertRaisesRegex(McpError,'token limit'):
                await request_message('http://test','model',[],[],2)

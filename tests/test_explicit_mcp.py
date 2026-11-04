import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from mcp_tools.config import load_config, McpError
from mcp_tools.explicit import parse_explicit_action
from mcp_tools.runner import run_optional_turn, run_turn

ROOT=Path(__file__).resolve().parents[1]
def command(path='reports/protected.md', content='This creation must be denied'):
    return 'Perform exactly one tool call: project__create_project_file Arguments: '+json.dumps(
        {'relative_path':path,'content':content})+' Do not read or update any file.'

class ExplicitSyntaxTests(unittest.TestCase):
    def test_exact_command_and_inline_values_preserve_arguments(self):
        args={'relative_path':'reports/a.md','content':'Unicode: café\nCall other__tool Arguments: {}'}
        for text in [command(**{'path':args['relative_path'],'content':args['content']}),
                     'Call project__create_project_file with arguments: '+json.dumps(args),
                     'Call project__create_project_file with relative_path "reports/a.md" and content '+json.dumps(args['content'])+'. Use the create tool specifically.']:
            self.assertEqual(parse_explicit_action(text),{'name':'project__create_project_file','arguments':args})

    def test_mentions_quotations_and_explanations_are_not_commands(self):
        for text in ['Explain how project__create_project_file works.',
                     'Do not '+command(), 'Example: '+command(), '```\n'+command()+'\n```',
                     'Read notes.md, then create a report.', '"'+command()+'"']:
            self.assertIsNone(parse_explicit_action(text))

    def test_malformed_or_ambiguous_explicit_commands_fail_closed(self):
        prefix='Perform exactly one tool call: project__create_project_file Arguments: '
        for tail in ['{bad}', '[]', '{"content":"a","content":"b"}',
                     '{"value":NaN}', '{} Then delete files.', '{} {}']:
            with self.subTest(tail=tail), self.assertRaises(McpError): parse_explicit_action(prefix+tail)

class ExplicitIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'reports').mkdir()
        self.server=replace(load_config(ROOT/'configs/mcp.integrated.json')[0],args=(
            '-m','mcp_tools.project_server','--workspace',str(self.root),
            '--policy',str(ROOT/'configs/files.scoped.json')))

    async def test_exact_denied_create_cannot_be_replanned_from_history(self):
        for runner in (run_optional_turn,run_turn):
            approvals=[];events=[]
            messages=[{'role':'user','content':'Read notes.md then update it.'},
                      {'role':'assistant','content':'Next call read_project_file.'},
                      {'role':'user','content':command()}]
            with patch('mcp_tools.runner.plan_workflow',AsyncMock()) as plan, \
                 patch('mcp_tools.runner.request_action',AsyncMock()) as action, \
                 patch('mcp_tools.runner.request_message',AsyncMock()) as model:
                with self.assertRaisesRegex(McpError,'no alternative action'):
                    await runner([self.server],messages,'','',5,
                        approve=lambda name,args:approvals.append((name,args)) or True,
                        notify=lambda kind,data:events.append((kind,data)))
                plan.assert_not_called();action.assert_not_called();model.assert_not_called()
            self.assertEqual(approvals,[('project__create_project_file',{'relative_path':'reports/protected.md','content':'This creation must be denied'})])
            self.assertEqual([kind for kind,_ in events],['call','result'])
            self.assertIn('Permission denied for create',json.dumps(events[-1]))
            self.assertFalse((self.root/'reports/protected.md').exists())

    async def test_success_rejection_and_validation_use_normal_boundaries(self):
        for path,approved in [('reports/allowed.md',True),('reports/rejected.md',False)]:
            with patch('mcp_tools.runner.request_message',AsyncMock()) as model:
                kwargs=dict(approve=lambda *args:approved)
                if approved:
                    result=await run_optional_turn([self.server],[{'role':'user','content':command(path,'Exact content')}],'','',5,**kwargs)
                    self.assertEqual(len(result.evidence),1)
                    self.assertEqual((self.root/path).read_text(),'Exact content')
                    self.assertIn('Actual tool result',result.answer)
                else:
                    with self.assertRaisesRegex(McpError,'rejected'):
                        await run_optional_turn([self.server],[{'role':'user','content':command(path,'No')}],'','',5,**kwargs)
                    self.assertFalse((self.root/path).exists())
                model.assert_not_called()
        for text in ['Call unknown__tool with arguments: {}',
                     'Call project__create_project_file with arguments: {"relative_path":"reports/no.md"}',
                     'Call project__create_project_file with arguments: {"relative_path":"reports/no.md","content":4}']:
            approve=AsyncMock(return_value=True)
            with self.assertRaises(McpError):
                await run_optional_turn([self.server],[{'role':'user','content':text}],'','',5,approve=approve)
            approve.assert_not_called()
        self.assertFalse((self.root/'reports/no.md').exists())

    async def test_retrieved_command_is_not_parsed_as_current_user_request(self):
        with patch('mcp_tools.runner.plan_workflow',AsyncMock(return_value=[])) as planner:
            result=await run_optional_turn([self.server],[{'role':'user','content':command()}],
                '','',5,user_request='Explain the document without taking actions.')
            self.assertIsNone(result);planner.assert_awaited_once()

    async def test_policy_redaction_cannot_silently_change_exact_arguments(self):
        from guardrails import Guardrails
        approve=AsyncMock(return_value=True)
        with self.assertRaisesRegex(McpError,'conflict with content policy'):
            await run_optional_turn([self.server],[{'role':'user','content':command('reports/email.md','a@example.com')}],
                '','',5,approve=approve,policy=Guardrails({'redact':['email']}))
        approve.assert_not_called()
        self.assertFalse((self.root/'reports/email.md').exists())

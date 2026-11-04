"""Exercise T4 grants using a real MCP server and disposable files (no LLM)."""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from mcp_tools.config import load_config
from mcp_tools.gateway import connect

ROOT = Path(__file__).resolve().parents[1]

async def verify():
    report = {'recorded_at':datetime.now(timezone.utc).isoformat(),
              'model_used':False, 'mocked_servers':False, 'checks':[]}
    def record(name, passed, observed):
        report['checks'].append({'name':name,'passed':bool(passed),'observed':observed})
    with tempfile.TemporaryDirectory(prefix='sensai-t4-') as directory:
        root=Path(directory)
        (root/'src/private').mkdir(parents=True)
        (root/'reports').mkdir()
        (root/'src/main.py').write_text('print(1)',encoding='utf-8')
        (root/'src/private/secret.md').write_text('PRIVATE_SENTINEL',encoding='utf-8')
        (root/'reports/protected.md').write_text('PROTECTED',encoding='utf-8')
        (root/'reports/link.py').symlink_to(root/'src/main.py')
        config=replace(load_config(ROOT/'configs/mcp.integrated.json')[0],args=(
            '-m','mcp_tools.project_server','--workspace',directory,
            '--policy',str(ROOT/'configs/files.scoped.json')))
        async with connect([config]) as gateway:
            async def call(tool, **arguments):
                return await gateway.call('project__'+tool,arguments)
            read=await call('read_project_file',relative_path='src/main.py')
            record('read_allowed_file',not read['isError'] and read['structuredContent']['content']=='print(1)',read)
            for name,path in [('private_read_denied','src/private/secret.md'),
                              ('traversal_denied','../README.md'),
                              ('symlink_denied','reports/link.py')]:
                result=await call('read_project_file',relative_path=path)
                record(name,result['isError'] and 'PRIVATE_SENTINEL' not in json.dumps(result),result)
            denied=await call('create_project_file',relative_path='src/new.py',content='print(2)')
            record('create_outside_grant_denied',denied['isError'] and not (root/'src/new.py').exists(),denied)
            created=await call('create_project_file',relative_path='reports/note.md',content='original')
            record('create_allowed_report',not created['isError'] and (root/'reports/note.md').read_text()=='original',created)
            duplicate=await call('create_project_file',relative_path='reports/note.md',content='overwrite')
            record('create_cannot_overwrite',duplicate['isError'] and (root/'reports/note.md').read_text()=='original',duplicate)
            read=await call('read_project_file',relative_path='reports/note.md')
            version=read['structuredContent']['sha256']
            updated=await call('write_project_file',relative_path='reports/note.md',content='updated',expected_sha256=version)
            record('write_allowed_existing_report',not updated['isError'] and (root/'reports/note.md').read_text()=='updated',updated)
            stale=await call('write_project_file',relative_path='reports/note.md',content='stale overwrite',expected_sha256=version)
            record('stale_version_denied',stale['isError'] and (root/'reports/note.md').read_text()=='updated',stale)
            for name,path,original in [('write_readonly_file_denied','src/main.py','print(1)'),
                                       ('explicit_deny_wins','reports/protected.md','PROTECTED')]:
                result=await call('write_project_file',relative_path=path,content='changed',
                                  expected_sha256=hashlib.sha256(original.encode()).hexdigest())
                record(name,result['isError'] and (root/path).read_text()==original,result)
            record('protected_files_unchanged',(root/'src/private/secret.md').read_text()=='PRIVATE_SENTINEL'
                   and (root/'src/main.py').read_text()=='print(1)',{'unchanged':True})
            report['protocol']=gateway.protocol_events
    report['passed']=all(c['passed'] for c in report['checks'])
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='local-output/t4-permissions.json')
    args=parser.parse_args()
    try:
        report=asyncio.run(verify())
    except Exception as error:
        report={'passed':False,'error':str(error)}
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    for check in report.get('checks',[]):
        print(('PASS' if check['passed'] else 'FAIL')+' '+check['name'])
    if 'error' in report: print(report['error'])
    print('Evidence: '+str(target))
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__': main()

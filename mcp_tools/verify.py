"""Reproducible real-protocol and isolation evidence, without an LLM or mocks."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from .config import load_config
from .gateway import connect

ROOT = Path(__file__).resolve().parents[1]

async def verify():
    report={'recorded_at':datetime.now(timezone.utc).isoformat(),
            'model_used':False, 'mocked_servers':False, 'checks':[], 'protocol':[]}
    def record(name,passed,observed):
        report['checks'].append({'name':name,'passed':bool(passed),'observed':observed})
    async with connect(load_config(ROOT/'configs/mcp.demo.json')) as gateway:
        result=await gateway.call('time__get_current_time',{'timezone':'Europe/Madrid'})
        record('third_party_time', not result['isError'] and 'Europe/Madrid' in json.dumps(result),result)
        result=await gateway.call('project__read_project_file',{'relative_path':'project_notes.md'})
        record('custom_project_read',not result['isError'] and 'SQLite' in json.dumps(result),result)
        result=await gateway.call('project__read_project_file',{'relative_path':'../README.md'})
        record('traversal_denied',result['isError'],result)
        report['protocol']+=gateway.protocol_events
    with tempfile.TemporaryDirectory() as directory:
        sentinel=Path(directory)/'host.txt';sentinel.write_text('HOST_SENTINEL_NOT_FOR_MODEL')
        probes=[('calculation','print(sum(range(11)))','55'),
                ('host_read_denied',f'open({str(sentinel)!r}).read()',None),
                ('network_denied','import socket; socket.socket()',None),
                ('child_process_denied','import os; os.fork()',None),
                ('write_denied',"open('/work/code.py', 'w').write('changed')",None),
                ('memory_limited','bytearray(300*1024*1024)',None),
                ('traceback_returned','print(1/0)','ZeroDivisionError')]
        async with connect(load_config(ROOT/'configs/mcp.sandbox.json')) as gateway:
            for name,source,expected in probes:
                result=await gateway.call('python__execute_python',{'code':source})
                observed=result.get('structuredContent',{})
                passed=(observed.get('status')=='ok' and observed.get('stdout','').strip()==expected
                        if name=='calculation' else
                        observed.get('status')=='error' and observed.get('exit_code') not in (None,0)
                        and 'Traceback' in observed.get('stderr',''))
                if name=='traceback_returned': passed=passed and expected in observed.get('stderr','')
                record(name,passed,observed)
            report['protocol']+=gateway.protocol_events
        record('host_file_unchanged',sentinel.read_text()=='HOST_SENTINEL_NOT_FOR_MODEL',{'unchanged':sentinel.read_text()=='HOST_SENTINEL_NOT_FOR_MODEL'})
    report['passed']=all(check['passed'] for check in report['checks'])
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='local-output/b-protocol-isolation.json')
    args=parser.parse_args()
    try: report=asyncio.run(verify())
    except Exception as error:
        report={'passed':False,'error':str(error),'note':'Verification interrupted; no claim of complete coverage.'}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    for check in report.get('checks',[]):
        print(('PASS' if check['passed'] else 'FAIL')+' '+check['name'])
    print('Evidence: '+str(path))
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__': main()

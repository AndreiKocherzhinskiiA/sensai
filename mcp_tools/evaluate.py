"""Live, repeatable Python-recovery evaluation using real Ollama and MCP.

Only the configured isolated Python tool is auto-approved in this test harness.
Production adapters still ask for every execution. Reports include all programs
and actual observations; success requires expected stdout, not model prose.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import load_config
from .runner import run_python_task

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('undefined_variable', 'values = [10, 20, 30]\nprint(sum(values) / len(value))',
     'Calculate the arithmetic mean of the values. Fix errors and execute the corrected code.', '20.0'),
    ('missing_measurement', 'measurements = ["10", "20", "N/A", "30"]\nnumbers = [float(x) for x in measurements]\nprint(sum(numbers) / len(numbers))',
     'Calculate the arithmetic mean, skipping missing measurements marked N/A. Fix errors and execute the corrected code.', '20.0'),
    ('syntax', 'for x in [1, 2, 3]\n    print(x*x)',
     'Print each square of 1, 2, 3 on a separate line. Fix errors and execute the corrected code.', '1\n4\n9'),
]

async def evaluate(model, host, repeats):
    servers = load_config(ROOT / 'configs/mcp.sandbox.json')
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'model': model,
              'real_model': True, 'real_mcp': True, 'test_only_auto_approval': True, 'cases': []}
    for repetition in range(repeats):
        for name, code, goal, expected in CASES:
            events = []
            row = {'name': name, 'repetition': repetition + 1, 'initial_code': code,
                   'goal': goal, 'expected_stdout': expected, 'passed': False}
            try:
                result = await run_python_task(servers, code, goal, host, model, 60,
                    approve=lambda name, args: name == 'python__execute_python',
                    notify=lambda kind, data: events.append({'kind': kind, 'data': data}))
                first, last = result.evidence[0], result.evidence[-1]
                execution = last['result'].get('structuredContent', {})
                row['passed'] = (first['arguments']['code'] == code
                    and first['result']['structuredContent']['status'] == 'error'
                    and len(result.evidence) >= 2 and execution.get('status') == 'ok'
                    and execution.get('exit_code') == 0
                    and execution.get('stdout', '').strip() == expected)
                row['answer'] = result.answer
            except Exception as error:
                row['error'] = str(error)
            row['events'] = events
            report['cases'].append(row)
            print(('PASS' if row['passed'] else 'FAIL') + ' ' + name + ' #' + str(repetition + 1), flush=True)
    report['passed'] = all(row['passed'] for row in report['cases'])
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3:4b')
    parser.add_argument('--host', default='http://127.0.0.1:11434')
    parser.add_argument('--repeats', type=int, default=2, choices=range(1, 11))
    parser.add_argument('--output', default='local-output/b-live-recovery.json')
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.model, args.host, args.repeats))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Evidence: ' + str(path))
    raise SystemExit(0 if report['passed'] else 1)

if __name__ == '__main__':
    main()

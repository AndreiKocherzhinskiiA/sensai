"""Explicit-source CLI adapter for the shared Python execution/repair pipeline."""
import argparse
import asyncio
from pathlib import Path
from .config import load_config, McpError
from .runner import run_python_task, confirm_tool, print_event
from guardrails import Guardrails, GuardrailError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-file',required=True)
    parser.add_argument('--goal',required=True)
    parser.add_argument('--model',default='qwen3:4b')
    parser.add_argument('--host',default='http://localhost:11434')
    parser.add_argument('--mcp-config',default='configs/mcp.sandbox.json')
    parser.add_argument('--guardrails')
    parser.add_argument('--timeout',type=float,default=60)
    args=parser.parse_args()
    if not 0 < args.timeout <= 300: parser.error('timeout must be between 0 and 300 seconds')
    try:
        with Path(args.code_file).open('rb') as stream: raw=stream.read(16385)
        if len(raw)>16384: raise McpError('Source file exceeds 16 KiB.')
        code=raw.decode('utf-8')
        policy=Guardrails.from_file(args.guardrails) if args.guardrails else None
        print('Initial source: user-supplied file. Every execution requires approval.')
        result=asyncio.run(run_python_task(load_config(args.mcp_config),code,args.goal,
            args.host,args.model,args.timeout,approve=confirm_tool,notify=print_event,policy=policy))
        print('Answer: '+result.answer)
    except (OSError, UnicodeError, McpError, GuardrailError) as error:
        print('Execution failed: '+str(error))
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print('Cancelled. Completed actions are not undone.')
        raise SystemExit(130)

if __name__=='__main__': main()

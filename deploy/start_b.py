"""Launch Member B's integrated tool workspace on Linux/WSL."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3:4b')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--install-search', action='store_true', help='install the pinned search provider on first launch')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run this command inside Linux/WSL with the project virtual environment active.')
    for dependency in ('mcp', 'mcp_server_time', 'uvicorn'):
        if importlib.util.find_spec(dependency) is None:
            parser.error('Missing dependency. Run: python -m pip install -r requirements-mcp.txt')
    sys.path.insert(0, str(ROOT))
    from sandbox.runner import bubblewrap, SandboxError
    try:
        bubblewrap()
    except SandboxError as error:
        parser.error(str(error))
    host = (os.environ.get('SENSAI_OLLAMA_HOST') or os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')).rstrip('/')
    if not host.startswith(('http://', 'https://')):
        host = 'http://' + host
    try:
        with urllib.request.urlopen(host + '/api/tags', timeout=5) as response:
            models = json.load(response)['models']
        if args.model not in {item['name'] for item in models}:
            parser.error('Model not installed. Run: ollama pull ' + args.model)
    except (OSError, ValueError, KeyError) as error:
        parser.error('Cannot reach Ollama. Start ollama serve. Details: ' + str(error))
    os.chdir(ROOT)
    workspace = ROOT / 'local-output'
    workspace.mkdir(exist_ok=True)
    (workspace / 'reports').mkdir(exist_ok=True)
    notes = workspace / 'project_notes.md'
    try:
        with notes.open('x', encoding='utf-8') as target:
            target.write((ROOT / 'examples/project_notes.md').read_text(encoding='utf-8'))
    except FileExistsError:
        pass
    os.environ.update(SENSAI_OLLAMA_HOST=host, SENSAI_MODEL=args.model, SENSAI_MODE='ollama',
        SENSAI_MCP_CONFIG=str(ROOT / 'configs/mcp.integrated.json'),
        SENSAI_GUARDRAILS=str(ROOT / 'guardrails/demo.json'),
        SENSAI_SEARCH_URL='http://127.0.0.1:8088')
    print('Workspace: ' + str(workspace), flush=True)
    print('Web: http://127.0.0.1:' + str(args.port), flush=True)
    from deploy.services import run_stack, port_open
    if not 1 <= args.port <= 65535:
        parser.error('Web port must be between 1 and 65535.')
    if args.port == 8088:
        parser.error('Port 8088 is reserved for the local search provider; choose another web --port.')
    if port_open('127.0.0.1',args.port):
        parser.error('Web port is already in use. Stop the old web server or choose another --port.')
    search_command=[sys.executable,str(ROOT/'deploy/search/local.py')]
    if args.install_search:search_command.append('--install')
    try:
        code=run_stack([sys.executable,'-m','uvicorn','web.app:app','--host','127.0.0.1','--port',str(args.port)],
                       search_command,'http://127.0.0.1:8088',search_timeout=600 if args.install_search else 40)
    except (OSError,RuntimeError) as error:
        parser.error(str(error))
    raise SystemExit(code)


if __name__ == '__main__':
    main()

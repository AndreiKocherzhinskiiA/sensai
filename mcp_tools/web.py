"""Shared MCP loop and local-operator connection management."""
import asyncio
from contextlib import suppress, asynccontextmanager
import secrets
import os
import ipaddress
from urllib.parse import urlsplit
from fastapi import Request
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, StrictBool, Field

from guardrails import GuardrailError
from .config import McpError, load_config
from structured.output import strict_json


class Decision(BaseModel):
    approval_id: str = Field(min_length=1, max_length=128)
    approved: StrictBool
    model_config = {'extra': 'forbid'}


def get_manager(app):
    from .manager import ConnectionManager
    manager=getattr(app.state, 'mcp_manager', None)
    configs=app.state.mcp_servers
    if manager is None or manager.source_configs is not configs:
        state_path=os.getenv('SENSAI_MCP_STATE', 'local-output/mcp-registry.json')
        manager=ConnectionManager(configs or (), app.state.guardrails, state_path)
        manager.source_configs=configs
        app.state.mcp_manager=manager
    return manager


def operator_request(request):
    # Only a local operator may register executable commands. Browser/model
    # requests cannot use a cross-origin page to mutate this local registry.
    try:
        local=ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local=False
    origin=request.headers.get('origin')
    if not local or (origin and urlsplit(origin).netloc != request.headers.get('host')):
        raise HTTPException(403,'MCP management is restricted to the local application.')
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise HTTPException(415,'Use application/json for MCP management.')


def install_routes(app, config_path=None):
    app.state.mcp_servers = load_config(config_path) if config_path else None
    app.state.mcp_manager = None
    if app.state.mcp_servers:
        # Fail at startup with an actionable message when configured but not installed.
        try:
            from . import runner  # noqa: F401
        except ImportError as error:
            raise RuntimeError('Install requirements-mcp.txt before enabling web MCP.') from error

    @app.get('/api/mcp')
    async def status():
        return {'enabled': bool(app.state.mcp_servers), 'max_calls': 4,
                'approval_timeout_seconds': 120,
                'default_model': app.state.default_model, 'default_mode': app.state.mode, 'management_enabled': True}

    @app.get('/api/mcp/tools')
    async def tools():
        if not app.state.mcp_servers:
            raise HTTPException(503, 'MCP is disabled. Set SENSAI_MCP_CONFIG on the server.')
        from .runner import list_tools
        try:
            return await list_tools(app.state.mcp_servers, app.state.guardrails, get_manager(app))
        except McpError as error:
            raise HTTPException(502, str(error)) from error

    previous_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def managed_lifespan(application):
        async with previous_lifespan(application) as state:
            manager=get_manager(application)
            application.state.mcp_servers=tuple(c.config for c in manager.connections.values())
            manager.source_configs=application.state.mcp_servers
            try:
                yield state
            finally:
                await manager.close()
    app.router.lifespan_context=managed_lifespan

    @app.get('/api/mcp/servers')
    async def servers():
        return get_manager(app).snapshots()

    @app.post('/api/mcp/servers')
    async def add_server(request: Request):
        operator_request(request)
        if len(await request.body())>65536: raise HTTPException(413,'Configuration too large.')
        manager=get_manager(app)
        try:
            row=strict_json((await request.body()).decode('utf-8'))
            result=await manager.add(row)
        except (ValueError,TypeError) as error:
            raise HTTPException(422,str(error)) from error
        app.state.mcp_servers=tuple(c.config for c in manager.connections.values())
        manager.source_configs=app.state.mcp_servers
        return result

    @app.post('/api/mcp/servers/{name}/{action}')
    async def control_server(name: str, action: str, request: Request):
        operator_request(request)
        manager=get_manager(app)
        try:
            if action == 'tools':
                if len(await request.body()) > 65536:
                    raise HTTPException(413, 'Configuration too large.')
                try:
                    settings = strict_json((await request.body()).decode('utf-8'))
                except (ValueError, UnicodeError) as error:
                    raise HTTPException(422, 'Invalid tool settings JSON.') from error
                result=await manager.set_tools(name,settings)
            else:
                result=await manager.control(name,action)
        except McpError as error:
            raise HTTPException(409,str(error)) from error
        app.state.mcp_servers=tuple(c.config for c in manager.connections.values())
        manager.source_configs=app.state.mcp_servers
        return result

    @app.post('/api/runs/{run_id}/mcp/decision')
    async def decide(run_id: str, decision: Decision):
        run = app.state.runs.get(run_id)
        pending = run.get('mcp_pending') if run else None
        if (not pending or run.get('cancelled') or pending['future'].done()
                or not secrets.compare_digest(pending['id'], decision.approval_id)):
            raise HTTPException(409, 'MCP approval is unknown, expired or already decided.')
        # This endpoint and the future live on the same event loop. No await between
        # checking and deciding: duplicate and cross-run decisions cannot race.
        pending['future'].set_result(decision.approved)
        return {'status': 'approved' if decision.approved else 'rejected'}


async def turn_events(run):
    """Own one SDK task until completion, cancellation or SSE disconnect.

    The shared loop allows at most four calls, so the queue has a fixed upper
    bound on event count (approval, call and result per call, plus final event).
    SDK contexts must be entered and exited inside the same worker task.
    """
    from .runner import run_turn, run_python_task, run_optional_turn
    events = asyncio.Queue()

    async def approve(name, arguments):
        future = asyncio.get_running_loop().create_future()
        pending = {'id': secrets.token_hex(24), 'future': future}
        run['mcp_pending'] = pending
        events.put_nowait(('approval_request', {
            'kind': 'mcp', 'tool': name, 'arguments': arguments,
            'approval_id': pending['id'], 'timeout_seconds': 120,
        }))
        try:
            return await asyncio.wait_for(future, run.get('mcp_approval_timeout', 120))
        except asyncio.TimeoutError as error:
            raise McpError('MCP approval expired; no further actions executed.') from error
        finally:
            run['mcp_pending'] = None

    def notify(kind, payload):
        if kind in ('plan', 'review', 'execution_review'):
            events.put_nowait(('loop_step', {'kind':'mcp', 'phase':kind, **payload}))
            return
        events.put_nowait(('tool_' + kind, {'kind': 'mcp', **payload}))
        if kind == 'result':
            events.put_nowait(('loop_step', {'kind':'mcp','phase':'observation',
                'tool':payload['tool'],'is_error':payload['result'].get('isError'),
                'message':'Actual tool result returned to the tool loop.'}))

    async def worker():
        try:
            events.put_nowait(('loop_step', {'kind':'mcp','phase':'start',
                'message':'MCP tool stage started. Only connected, enabled servers are available.'}))
            if run.get('python_code') is not None:
                result = await run_python_task(run['mcp_servers'], run['python_code'],
                    run['message'], run['host'], run['model'], run['timeout'], max_calls=4,
                    approve=approve, notify=notify, policy=run.get('guardrails'), manager=run.get('mcp_manager'))
            elif run.get('automatic_tools'):
                result = await run_optional_turn(run['mcp_servers'], run['messages'], run['host'],
                    run['model'], run['timeout'], max_calls=4,
                    approve=approve, notify=notify, policy=run.get('guardrails'), user_request=run['message'], manager=run.get('mcp_manager'))
            else:
                result = await run_turn(run['mcp_servers'], run['messages'], run['host'],
                                        run['model'], run['timeout'], max_calls=4,
                                        approve=approve, notify=notify, policy=run.get('guardrails'), user_request=run['message'], manager=run.get('mcp_manager'))
            events.put_nowait(('loop_step', {'kind':'mcp','phase':'complete',
                'tool_calls':len(result.evidence) if result else 0,
                'message':'Tool stage completed; response continues through output checks.'}))
            events.put_nowait(('answer', result))
        except asyncio.CancelledError:
            events.put_nowait(('cancelled', None))
            raise
        except (McpError, GuardrailError) as error:
            events.put_nowait(('failure', error))
        except Exception:
            events.put_nowait(('failure', McpError('MCP request failed; check the server configuration.')))

    task = asyncio.create_task(worker())
    # Cancellation can happen before the coroutine starts and reaches its handler.
    task.add_done_callback(lambda completed: events.put_nowait(('cancelled', None))
                           if completed.cancelled() else None)
    run['mcp_task'] = task
    try:
        while True:
            kind, payload = await events.get()
            if kind == 'failure':
                raise payload
            if kind == 'cancelled':
                return
            yield kind, payload
            if kind == 'answer':
                return
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        run['mcp_pending'] = None
        run['mcp_task'] = None

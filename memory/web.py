"""Web binding for built-in structured memory; no MCP dependency."""
import asyncio
from contextlib import suppress
import os
import secrets
from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool
from .store import MemoryStore, MemoryError
from . import pipeline


class Decision(BaseModel):
    approval_id: str = Field(min_length=1, max_length=128)
    approved: StrictBool
    model_config = {'extra': 'forbid'}


def enabled():
    return os.getenv('SENSAI_MEMORY_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')


def store_for(app):
    if getattr(app.state, 'memory_store', None) is None:
        app.state.memory_store = MemoryStore(os.getenv('SENSAI_MEMORY_DB', 'local-output/memory.sqlite3'),
            scope=os.getenv('SENSAI_MEMORY_SCOPE', 'default'))
    return app.state.memory_store


def install_routes(app):
    @app.get('/api/memory')
    def status():
        return {'enabled': enabled(), 'temporary_chats': False}

    @app.post('/api/runs/{run_id}/memory/decision')
    async def decide(run_id: str, decision: Decision):
        run = app.state.runs.get(run_id)
        pending = run.get('memory_pending') if run else None
        if (not pending or run.get('cancelled') or pending['future'].done()
                or not secrets.compare_digest(pending['id'], decision.approval_id)):
            raise HTTPException(409, 'Memory approval is unknown, expired or already decided.')
        pending['future'].set_result(decision.approved)
        return {'status': 'approved' if decision.approved else 'rejected'}


async def turn_events(app, run):
    """Prepare without mutation, then wait for the exact one-use approval."""
    store = store_for(app)
    history = [m for m in run['messages'][:-1] if m.get('role') in ('user', 'assistant')]
    task = asyncio.create_task(pipeline.prepare(store, run['message'], history,
        run['host'], run['model'], run['timeout'], run.get('guardrails'),
        session_store=app.state.session_store, current_session_id=run['session_id']))
    run['memory_task'] = task
    try:
        prepared = await task
        if run.get('cancelled') or prepared['action'] == 'none':
            return
        if prepared['action'] == 'previous_chat':
            observation = {'conversation_history': prepared['history']}
            yield 'tool_result', {'kind': 'memory', 'action': 'previous_chat', 'result': prepared['history']}
        else:
            observation = {'search': prepared['search']}
            yield 'tool_result', {'kind': 'memory', 'action': 'read', 'result': prepared['search']}
        proposal = prepared.get('proposal')
        if proposal is not None:
            future = asyncio.get_running_loop().create_future()
            token = secrets.token_hex(24)
            run['memory_pending'] = {'id': token, 'future': future}
            preview = {'action': prepared['action'], 'record': proposal}
            if prepared.get('before'):
                preview['previous_record'] = prepared['before']
            yield 'approval_request', {'kind': 'memory', 'action': prepared['action'],
                'arguments': preview, 'approval_id': token, 'timeout_seconds': 120}
            try:
                approved = await asyncio.wait_for(future, 120)
            except asyncio.TimeoutError as error:
                raise MemoryError('Memory approval expired; nothing changed.') from error
            finally:
                run['memory_pending'] = None
            if run.get('cancelled'):
                return
            if not approved:
                raise MemoryError('Memory change rejected; nothing changed.')
            # Source is supplied by the application, not invented by the model.
            source = f'Chat {run["session_id"]}; run {run["stream"].run_id}: ' + run['message']
            source = source[:1000]
            result = pipeline.commit(store, prepared, source)
            observation = {'operation': prepared['action'], 'receipt': result}
            yield 'tool_result', {'kind': 'memory', 'action': prepared['action'], 'result': result}
        elif prepared.get('status'):
            observation['status'] = prepared['status']
        run['messages'] = pipeline.inject(run['messages'], observation)
        if run.get('guardrails'):
            run['messages'] = run['guardrails'].messages(run['messages'])
        run['cache_context_independent'] = False
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        pending = run.pop('memory_pending', None)
        if pending and not pending['future'].done():
            pending['future'].cancel()

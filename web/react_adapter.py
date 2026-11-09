"""ReAct web adapter: async events and cancellable model streaming bridge."""
import asyncio
from contextlib import suppress
from core.chat import execute_turn_stream, ChatError
from core.react_runtime import events, calculate, CALCULATE_SCHEMA, LoopFailure
from structured.output import JsonOutput, StructuredOutputError
from web_search.context import filter_page, evidence_data

async def turn_events(run, searcher=None):
    tools = {'calculate': calculate}
    schemas = [CALCULATE_SCHEMA]
    policy = run.get('guardrails')
    if searcher is not None:
        async def search(query):
            if not isinstance(query, str) or not 1 <= len(query) <= 500:
                raise ValueError('query must contain 1 to 500 characters')
            page = filter_page(await searcher.search_async(query), policy)
            return evidence_data(page)
        tools['web_search'] = search
        schemas.append({'name': 'web_search', 'arguments': {'query': 'string, 1-500 characters'}})

    argument_schemas = {'calculate': {
        'operation': {'enum': ['add', 'subtract', 'multiply', 'divide']},
        'a': {'type': 'number'}, 'b': {'type': 'number'},
    }}
    if searcher is not None:
        argument_schemas['web_search'] = {'query': {'type': 'string', 'minLength': 1, 'maxLength': 500}}
    alternatives = [{
        'properties': {'final_answer': {'type': 'string', 'minLength': 1}},
        'required': ['final_answer'],
        'additionalProperties': False,
    }]
    for name, properties in argument_schemas.items():
        alternatives.append({
            'properties': {
                'action': {'const': name},
                'action_input': {'type': 'object', 'properties': properties,
                                 'required': list(properties), 'additionalProperties': False},
            },
            'required': ['action', 'action_input'],
            'additionalProperties': False,
        })
    output = JsonOutput({'type': 'object', 'oneOf': alternatives})

    async def generate(messages, remaining):
        import threading
        stop = threading.Event()
        run['react_stop'] = stop
        def collect():
            return ''.join(execute_turn_stream(
                host=run['host'], model=run['model'], to_send=messages,
                timeout=min(run['timeout'], remaining), stop_event=stop,
                guardrails=policy, json_output=output, cache_context_independent=False))
        try:
            return await asyncio.to_thread(collect)
        except (ChatError, StructuredOutputError) as exc:
            raise LoopFailure(str(exc)) from exc
        finally:
            stop.set()

    source = events(run['messages'], generate, tools, schemas, max_steps=6, timeout=90)
    async def consume():
        async for kind, data in source:
            await queue.put((kind, data))
    queue = asyncio.Queue()
    task = asyncio.create_task(consume())
    run['react_task'] = task
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), .1)
                yield item
            except asyncio.TimeoutError:
                if run.get('cancelled'):
                    task.cancel()
                    return
                if task.done():
                    await task
                    return
    finally:
        task.cancel()
        stop = run.get('react_stop')
        if stop is not None:
            stop.set()
        with suppress(asyncio.CancelledError, Exception):
            await task
        await source.aclose()

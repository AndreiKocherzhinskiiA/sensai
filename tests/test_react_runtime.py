import asyncio
import json
import pytest
from core.react_runtime import events, calculate, CALCULATE_SCHEMA, LoopFailure

def collect(generate, tools=None, max_steps=6, timeout=1):
    async def run():
        return [item async for item in events([{'role':'user','content':'test'}], generate,
            tools or {'calculate':calculate}, [CALCULATE_SCHEMA], max_steps, timeout)]
    return asyncio.run(run())

def test_dependent_actions_and_context():
    seen=[]
    async def model(messages, remaining):
        seen.append(messages)
        if len(seen) == 1:
            return json.dumps({'action':'calculate','action_input':{'operation':'multiply','a':37,'b':19}})
        observation = json.loads(messages[-1]['content'].split(': ', 1)[1])
        if len(seen) == 2:
            return json.dumps({'action':'calculate','action_input':{
                'operation':'multiply','a':observation['value'],'b':1.21}})
        return json.dumps({'final_answer':f'Subtotal: €703.00. Total: €{observation["value"]:.2f}.'})
    output=collect(model)
    assert output[-1] == ('answer', {'text':'Subtotal: €703.00. Total: €850.63.'})
    assert [data['result']['value'] for kind, data in output if kind == 'tool_result'] == [703, 850.63]
    assert '703' in seen[1][-1]['content']
    assert '850.63' in seen[2][-1]['content']
    assert output[-2][1]['tool_calls'] == 2
    assert seen[0][-1]['content'] == 'test'

def test_protocol_repair():
    replies=iter(['not JSON', '{"final_answer":"fixed"}'])
    async def model(messages, remaining): return next(replies)
    output=collect(model)
    assert any(x[1].get('status')=='protocol_error' for x in output)
    assert output[-1][1]['text']=='fixed'

def test_step_limit():
    async def model(messages, remaining): return '{}'
    with pytest.raises(LoopFailure, match='max_steps_reached'):
        collect(model,max_steps=2)

def test_timeout():
    async def model(messages, remaining):
        await asyncio.sleep(.1)
        return '{"final_answer":"late"}'
    with pytest.raises(LoopFailure, match='time_limit_exceeded'):
        collect(model,timeout=.01)

def test_tool_error_is_observed():
    replies=iter(['{"action":"calculate","action_input":{"operation":"divide","a":1,"b":0}}',
                  '{"final_answer":"Division failed"}'])
    async def model(messages, remaining): return next(replies)
    output=collect(model)
    assert next(data for kind,data in output if kind=='tool_result')['ok'] is False

def test_unknown_tool_not_executed():
    async def model(messages, remaining):
        return '{"action":"unknown","action_input":{}}'
    with pytest.raises(LoopFailure): collect(model,max_steps=1)

def test_cancel_propagates():
    async def model(messages, remaining): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): collect(model)

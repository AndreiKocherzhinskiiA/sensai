"""Persistent MCP sessions, each owned and closed by one asyncio task.

The SDK's AnyIO contexts must not be entered/exited by different request tasks.
An actor serializes calls to each external process. Views are generation-bound:
approval obtained before close/restart/disable cannot execute on a new session.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from .config import McpError, parse_config, config_row, tool_names
from .gateway import Gateway, connect


class Connection:
    def __init__(self, config, policy, enabled=True):
        self.config, self.policy = config, policy
        self.enabled = enabled
        self.state = 'disconnected' if enabled else 'disabled'
        self.generation = 0
        self.task = None
        self.gateway = None
        self.queue = asyncio.Queue(maxsize=32)
        self.history = deque(maxlen=256)
        self.lock = asyncio.Lock()
        self.error = None
        self.busy = False

    def record(self, event, **data):
        self.history.append({'at':datetime.now(timezone.utc).isoformat(), 'event':event,
                             'generation':self.generation, **data})

    async def start(self):
        async with self.lock:
            if not self.enabled:
                raise McpError('MCP server is disabled.')
            if self.state == 'connected':
                return
            if self.task and not self.task.done():
                raise McpError('MCP server is changing state; retry shortly.')
            self.generation += 1
            self.state, self.error = 'connecting', None
            ready = asyncio.get_running_loop().create_future()
            self.task = asyncio.create_task(self.worker(ready))
            await asyncio.shield(ready)

    async def worker(self, ready):
        active = None
        try:
            async with connect([self.config], self.policy) as gateway:
                self.gateway = gateway
                self.state = 'connected'
                for event in gateway.protocol_events:
                    self.record('protocol', **event)
                ready.set_result(None)
                while True:
                    name, args, generation, active = await self.queue.get()
                    if active.cancelled():
                        continue
                    if generation != self.generation or not self.enabled:
                        active.set_exception(McpError('MCP connection changed; submit a new request.'))
                        continue
                    self.busy = True
                    self.record('call_started', tool=name)
                    operation = asyncio.create_task(gateway.call(name,args))
                    try:
                        done, _ = await asyncio.wait((operation,active), return_when=asyncio.FIRST_COMPLETED)
                        if active in done:
                            operation.cancel()
                            with suppress(asyncio.CancelledError): await operation
                            self.record('call_cancelled',tool=name)
                        else:
                            result = operation.result()
                            self.record('call_finished',tool=name,is_error=result['isError'])
                            if not active.done(): active.set_result(result)
                    except Exception as error:
                        self.record('call_failed',tool=name)
                        if not active.done(): active.set_exception(McpError(str(error)))
                        if gateway.failed_servers:
                            raise McpError('MCP transport failed; restart this server. Execution outcome may be unknown.')
                    finally:
                        if not operation.done():
                            operation.cancel()
                            with suppress(asyncio.CancelledError): await operation
                        self.busy = False
        except asyncio.CancelledError:
            if not ready.done(): ready.set_exception(McpError('MCP connection closed during startup.'))
            raise
        except Exception as error:
            self.state, self.error = 'failed', str(error)
            self.record('connection_failed',message=self.error)
            if not ready.done(): ready.set_exception(McpError(self.error))
        finally:
            self.gateway = None
            if active is not None and not active.done():
                active.set_exception(McpError('MCP connection closed during call; execution outcome may be unknown.'))
            while not self.queue.empty():
                *_, future = self.queue.get_nowait()
                if not future.done(): future.set_exception(McpError('MCP connection closed before execution.'))

    async def stop(self, state='closed'):
        async with self.lock:
            self.generation += 1
            self.state = 'closing'
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError): await self.task
            self.state, self.busy = state, False
            self.record(state)

    async def call(self, name, args, generation):
        if not self.enabled or self.state != 'connected' or generation != self.generation:
            raise McpError('MCP connection changed or was disabled; submit a new request.')
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((name,args,generation,future))
        except asyncio.QueueFull as error:
            raise McpError('MCP server queue is full; retry later.') from error
        try:
            return await future
        finally:
            if not future.done(): future.cancel()

    def snapshot(self):
        tools = self.gateway.catalog.values() if self.gateway else ()
        initialized = next((e for e in reversed(self.history) if e.get('method')=='initialize'),None)
        return {'name':self.config.name,'transport':self.config.transport,'enabled':self.enabled,
                'status':self.state,'generation':self.generation,'busy':self.busy,'error':self.error,
                'server_info':initialized.get('server_info') if initialized else None,
                'protocol_version':initialized.get('protocol_version') if initialized else None,
                'server_instructions':initialized.get('instructions') if initialized else None,
                'tools':[{'name':t.name,'remote_name':t.remote_name,'enabled':t.name in self.gateway.tools,
                          'description':t.description,'inputSchema':t.output.schema,
                          'approval':'automatic' if t.remote_name in t.server.auto_approve else 'ask'} for t in tools],
                'discovery': 'all' if self.config.allow_tools is None else 'selected',
                'events':list(self.history)}


class ManagedGateway(Gateway):
    def __init__(self, connections, policy):
        super().__init__(policy)
        self.bindings = {}
        for connection in connections:
            if connection.enabled and connection.state == 'connected':
                if connection.gateway.tools:
                    self.server_guidance.update(connection.gateway.server_guidance)
                for name, tool in connection.gateway.tools.items():
                    self.tools[name] = tool
                    self.bindings[name] = (connection,connection.generation)
        if len(json.dumps(self.server_guidance).encode('utf-8')) > 32768:
            raise McpError('Combined MCP usage notes exceed 32 KiB; disable unused servers.')
        if len(json.dumps([t.definition() for t in self.tools.values()])) > 262144:
            raise McpError('Combined MCP catalogue exceeds 256 KiB; disable unused tools or servers.')

    async def call(self,name,arguments):
        args=self.prepare(name,arguments)
        connection,generation=self.bindings[name]
        return await connection.call(name,args,generation)


class ConnectionManager:
    def __init__(self, configs, policy=None, state_path=None):
        self.policy, self.state_path = policy, Path(state_path) if state_path else None
        self.connections = {c.name:Connection(c,policy) for c in configs}
        self.lock = asyncio.Lock()
        if self.state_path and self.state_path.exists():
            saved=json.loads(self.state_path.read_text())
            parsed=parse_config({'servers':saved['servers']},self.state_path) if saved['servers'] else ()
            self.connections={c.name:Connection(c,policy,c.name not in saved['disabled']) for c in parsed}

    def persist(self):
        if self.state_path:
            data={'servers':[config_row(c.config) for c in self.connections.values()],
                  'disabled':[c.config.name for c in self.connections.values() if not c.enabled]}
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.state_path.with_suffix('.tmp')
            temporary.touch(mode=0o600, exist_ok=True)
            temporary.chmod(0o600)
            temporary.write_text(json.dumps(data,indent=2))
            temporary.replace(self.state_path)

    async def add(self,row):
        async with self.lock:
            single = isinstance(row, dict) and 'name' in row
            configs = parse_config({'servers':[row]} if single else row, Path.cwd()/'runtime.json')
            if any(c.name in self.connections for c in configs):
                raise McpError('MCP server name already exists; use a different name or remove the old entry.')
            if len(self.connections) + len(configs) > 16:
                raise McpError('At most 16 MCP servers can be registered.')
            for config in configs:
                self.connections[config.name]=Connection(config,self.policy)
            self.persist()
            result = [self.connections[c.name].snapshot() for c in configs]
            return result[0] if single else result

    async def set_tools(self, name, settings):
        async with self.lock:
            connection = self.connections.get(name)
            if not connection or connection.state != 'connected':
                raise McpError('Connect the server before changing its tool permissions.')
            if not isinstance(settings, dict) or set(settings) != {'allow_tools', 'auto_approve'}:
                raise McpError('Supply allow_tools and auto_approve lists.')
            allow = tool_names(settings['allow_tools'], 'allow_tools')
            auto = tool_names(settings['auto_approve'], 'auto_approve')
            discovered = {t.remote_name for t in connection.gateway.catalog.values()}
            if not set(auto) <= set(allow) or not set(allow) <= discovered:
                raise McpError('Choose discovered tools; automatic approval requires an enabled tool.')
            # Close the old generation before applying policy: stale approvals
            # and queued calls cannot outlive a permission change.
            await connection.stop()
            connection.config = replace(connection.config, allow_tools=allow, auto_approve=auto)
            self.persist()
            await connection.start()
            return connection.snapshot()

    async def control(self,name,action):
        async with self.lock:
            connection=self.connections.get(name)
            if not connection: raise McpError('Unknown MCP server.')
            if action=='disable':
                connection.enabled=False
                await connection.stop('disabled')
            elif action=='enable':
                connection.enabled=True
                if connection.state == 'disabled': connection.state='disconnected'
                await connection.start()
            elif action=='connect': await connection.start()
            elif action=='close': await connection.stop()
            elif action=='restart':
                if not connection.enabled: raise McpError('Enable the server before restarting it.')
                await connection.stop()
                await connection.start()
            elif action=='remove':
                await connection.stop()
                del self.connections[name]
            else: raise McpError('Unknown MCP lifecycle action.')
            self.persist()
            return connection.snapshot()

    async def discover(self):
        # A broken optional server must not take down unrelated tools or chat.
        async def start(connection):
            if connection.enabled and connection.state in ('disconnected','connecting'):
                with suppress(McpError): await connection.start()
        await asyncio.gather(*(start(c) for c in list(self.connections.values())))

    @asynccontextmanager
    async def session(self,policy=None):
        await self.discover()
        yield ManagedGateway(list(self.connections.values()),policy or self.policy)

    def snapshots(self):return [c.snapshot() for c in self.connections.values()]

    async def close(self):
        await asyncio.gather(*(c.stop() for c in list(self.connections.values())))

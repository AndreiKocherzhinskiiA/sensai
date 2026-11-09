"""Independent SDK client: demonstrate Sensai's server without its chatbot."""
import asyncio
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path(__file__).resolve().parents[1]
    params=StdioServerParameters(command=sys.executable,cwd=str(root),args=[
        '-m','mcp_tools.project_server','--workspace',str(root/'examples')])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            info=await client.initialize()
            tools=await client.list_tools()
            result=await client.call_tool('read_project_file',{'relative_path':'project_notes.md'})
            assert not result.isError
            print('Server:',info.serverInfo.name)
            print('Negotiated protocol:',info.protocolVersion)
            print('Tools:',', '.join(t.name for t in tools.tools))
            for block in result.content:
                if block.type=='text':print(block.text)
            print('PASS: standard SDK client -> Sensai MCP server')

if __name__=='__main__':asyncio.run(main())

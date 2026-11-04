"""Workspace capabilities exposed through real MCP; policy is operator-owned."""
import argparse
import hashlib
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from file_access.reader import WorkspaceReader
from file_access.writer import WorkspaceWriter
from file_access.policy import FilePolicy

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--policy')
    args = parser.parse_args()
    policy = FilePolicy.from_file(args.policy) if args.policy else FilePolicy()
    access = WorkspaceWriter if policy.can_create or policy.can_write else WorkspaceReader
    with access(args.workspace, policy) as workspace:
        server = FastMCP('sensai-project')

        @server.tool(description='Read a UTF-8 file and obtain its SHA-256 version. Allowed: '
                     + repr(policy.read_paths) + '; denied: ' + repr(policy.deny_paths))
        def read_project_file(relative_path: str) -> CallToolResult:
            """Read a UTF-8 project file beneath the configured workspace."""
            snapshot = workspace.read_text(relative_path)
            return CallToolResult(isError=False, content=[TextContent(type='text', text=snapshot.content)],
                structuredContent={'path': snapshot.path, 'content': snapshot.content,
                    'sha256': hashlib.sha256(snapshot.content.encode('utf-8')).hexdigest(),
                    'size_bytes': snapshot.size_bytes})

        if policy.can_create:
            @server.tool(description='Create a NEW UTF-8 file; never overwrite. Allowed: '
                         + repr(policy.create_paths) + '; denied: ' + repr(policy.deny_paths)
                         + '. The host must approve this exact path and content.')
            def create_project_file(relative_path: str, content: str) -> dict:
                """Create a NEW UTF-8 file in the workspace; never overwrite.
                The MCP host must obtain approval for this exact path and content.
                """
                plan = workspace.prepare_create(relative_path, content)
                workspace.decide(plan.id, True)
                return {'created': plan.path, 'size_bytes': plan.size_bytes}

        if policy.can_write:
            @server.tool(description='Update an EXISTING UTF-8 file. First read it to obtain sha256; '
                         'pass that unchanged as expected_sha256. Allowed write paths: '
                         + repr(policy.write_paths) + '; denied: ' + repr(policy.deny_paths)
                         + '. The host must approve this exact path, new content and expected version.')
            def write_project_file(relative_path: str, content: str, expected_sha256: str) -> dict:
                plan = workspace.prepare_write(relative_path, content, expected_sha256)
                workspace.decide(plan.id, True)
                return {'updated': plan.path, 'size_bytes': plan.size_bytes,
                        'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}

        server.run(transport='stdio')

if __name__ == '__main__':
    main()

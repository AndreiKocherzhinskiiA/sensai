"""T4 operation/path grants, checked before target I/O, and versioned writes."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from file_access.policy import FilePolicy
from file_access.reader import WorkspaceReader, FileAccessError, SUPPORTED
from file_access.writer import WorkspaceWriter

ROOT = Path(__file__).resolve().parents[1]
def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

@unittest.skipUnless(SUPPORTED, 'Linux/WSL descriptors required')
class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('src/private', 'reports', 'src2'):
            (self.root/directory).mkdir(parents=True)
        for name, content in {'src/main.py':'print(1)', 'src/private/secret.md':'PRIVATE_VALUE',
            'reports/note.md':'original', 'reports/protected.md':'PROTECTED', 'src2/no.md':'NO'}.items():
            (self.root/name).write_text(content)
        self.policy = FilePolicy.from_file(ROOT/'configs/files.scoped.json')
        self.writer = WorkspaceWriter(self.root, self.policy)
        self.addCleanup(self.writer.close)

    def test_denied_actions_perform_no_target_io(self):
        actions = [lambda:self.writer.read_text('src/private/secret.md'),
                   lambda:self.writer.read_text('src2/no.md'),
                   lambda:self.writer.prepare_create('src/new.py','print(2)'),
                   lambda:self.writer.prepare_write('src/main.py','print(2)',digest('print(1)')),
                   lambda:self.writer.prepare_write('reports/protected.md','changed',digest('PROTECTED'))]
        for action in actions:
            with patch('os.open') as opened, patch('os.stat') as stated, patch('os.read') as read:
                with self.assertRaisesRegex(FileAccessError,'Permission denied'):
                    action()
                opened.assert_not_called()
                stated.assert_not_called()
                read.assert_not_called()

    def test_read_create_write_permissions_are_independent(self):
        self.assertEqual(self.writer.read_text('src/main.py').content,'print(1)')
        plan=self.writer.prepare_create('reports/new.md','hello')
        self.writer.decide(plan.id,True)
        plan=self.writer.prepare_write('reports/new.md','updated',digest('hello'))
        self.writer.decide(plan.id,True)
        self.assertEqual(self.writer.read_text('reports/new.md').content,'updated')
        with self.assertRaises(FileAccessError): self.writer.prepare_create('reports/new.md','overwrite')
        with self.assertRaises(FileAccessError): self.writer.prepare_write('reports/missing.md','new',digest(''))
        self.assertFalse((self.root/'reports/missing.md').exists())

    def test_rejected_and_replayed_update_do_not_write(self):
        plan=self.writer.prepare_write('reports/note.md','changed',digest('original'))
        self.assertFalse(self.writer.decide(plan.id,False))
        with self.assertRaises(FileAccessError): self.writer.decide(plan.id,True)
        self.assertEqual((self.root/'reports/note.md').read_text(),'original')

    def test_changed_version_during_approval_is_preserved(self):
        plan=self.writer.prepare_write('reports/note.md','changed',digest('original'))
        (self.root/'reports/note.md').write_text('external update')
        with self.assertRaisesRegex(FileAccessError,'changed'): self.writer.decide(plan.id,True)
        self.assertEqual((self.root/'reports/note.md').read_text(),'external update')

    def test_replaced_inode_with_identical_content_is_rejected(self):
        plan=self.writer.prepare_write('reports/note.md','changed',digest('original'))
        (self.root/'reports/note.md').rename(self.root/'reports/old.md')
        (self.root/'reports/note.md').write_text('original')
        with self.assertRaisesRegex(FileAccessError,'changed'): self.writer.decide(plan.id,True)
        self.assertEqual((self.root/'reports/note.md').read_text(),'original')

    def test_policy_is_checked_again_at_execution_before_io(self):
        for operation in ('create','write'):
            self.writer.policy=self.policy
            plan=(self.writer.prepare_create('reports/new.md','new') if operation=='create' else
                  self.writer.prepare_write('reports/note.md','changed',digest('original')))
            self.writer.policy=replace(self.policy,create_paths=(),write_paths=())
            with patch('os.open') as opened, patch('os.stat') as stated:
                with self.assertRaisesRegex(FileAccessError,'Permission denied'): self.writer.decide(plan.id,True)
                opened.assert_not_called(); stated.assert_not_called()

    def test_symlink_hardlink_and_parent_swap_cannot_redirect_update(self):
        outside=self.root/'src/main.py'
        (self.root/'reports/link.py').symlink_to(outside)
        os.link(outside,self.root/'reports/hard.py')
        for name in ('reports/link.py','reports/hard.py'):
            with self.assertRaises(FileAccessError): self.writer.prepare_write(name,'changed',digest('print(1)'))
        plan=self.writer.prepare_write('reports/note.md','changed',digest('original'))
        (self.root/'reports').rename(self.root/'old-reports')
        (self.root/'reports').symlink_to(self.root/'src',target_is_directory=True)
        with self.assertRaises(FileAccessError): self.writer.decide(plan.id,True)
        self.assertEqual(outside.read_text(),'print(1)')
        self.assertEqual((self.root/'old-reports/note.md').read_text(),'original')

    def test_invalid_hash_and_content_never_open_target(self):
        for value, version in [('new','bad'), ('\0',digest('original')), ('\ud800',digest('original'))]:
            with patch('os.open') as opened, self.assertRaises(FileAccessError):
                self.writer.prepare_write('reports/note.md',value,version)
            opened.assert_not_called()

    def test_advisory_lock_prevents_overlapping_cooperating_updates(self):
        import fcntl
        plan=self.writer.prepare_write('reports/note.md','changed',digest('original'))
        with (self.root/'reports/note.md').open('r+') as other:
            fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(FileAccessError): self.writer.decide(plan.id,True)
        self.assertEqual((self.root/'reports/note.md').read_text(),'original')

    def test_write_only_grant_does_not_enable_read_or_create(self):
        policy=replace(self.policy,read_paths=(),create_paths=(),allow_create=False)
        with WorkspaceWriter(self.root,policy) as writer:
            with self.assertRaises(FileAccessError): writer.read_text('reports/note.md')
            with self.assertRaises(FileAccessError): writer.prepare_create('reports/new.md','new')
            plan=writer.prepare_write('reports/note.md','changed',digest('original'))
            writer.decide(plan.id,True)
        self.assertEqual((self.root/'reports/note.md').read_text(),'changed')

    def test_exact_file_grant_and_directory_boundary(self):
        policy=replace(self.policy,read_paths=('src/main.py',),deny_paths=())
        with WorkspaceReader(self.root,policy) as reader:
            self.assertEqual(reader.read_text('src/main.py').content,'print(1)')
            for name in ('src/private/secret.md','src2/no.md','src/main.py/../main.py'):
                with self.assertRaises(FileAccessError): reader.read_text(name)

class PolicyParsingTests(unittest.TestCase):
    def test_invalid_grants_and_unknown_operations_are_denied(self):
        for rule in ('../src/', '/src/', 'src//', 'src/*', 'C:/src/', 'src\\file.md'):
            with self.subTest(rule=rule), self.assertRaises(ValueError): FilePolicy(read_paths=(rule,))
        for path in ('../file.md', '/file.md', 'src/../file.md', 'src//file.md', 'C:/file.md', '\ud800.md'):
            self.assertFalse(FilePolicy().permits('read',path))
        self.assertFalse(FilePolicy().permits('delete','file.md'))
        self.assertFalse(FilePolicy().permits('write','file.md'))
        self.assertFalse(FilePolicy().permits('create','file.md'))

    def test_incomplete_permissions_do_not_fall_back_to_full_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'policy.json'
            path.write_text(json.dumps({'extensions':['.md'],'max_bytes':100,'allow_create':True,
                                        'permissions':{'read':['src/']}}))
            with self.assertRaises(ValueError): FilePolicy.from_file(path)

class McpPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_scoped_permissions_and_update(self):
        from mcp_tools.config import load_config
        from mcp_tools.gateway import connect
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'src/private').mkdir(parents=True); (root/'reports').mkdir()
            (root/'src/main.py').write_text('print(1)')
            (root/'src/private/secret.md').write_text('PRIVATE_VALUE')
            config=replace(load_config(ROOT/'configs/mcp.integrated.json')[0],
                args=('-m','mcp_tools.project_server','--workspace',directory,'--policy',str(ROOT/'configs/files.scoped.json')))
            async with connect([config]) as gateway:
                denied=await gateway.call('project__read_project_file',{'relative_path':'src/private/secret.md'})
                self.assertTrue(denied['isError']); self.assertNotIn('PRIVATE_VALUE',json.dumps(denied))
                denied=await gateway.call('project__create_project_file',{'relative_path':'src/new.py','content':'x'})
                self.assertTrue(denied['isError']); self.assertFalse((root/'src/new.py').exists())
                created=await gateway.call('project__create_project_file',{'relative_path':'reports/note.md','content':'original'})
                self.assertFalse(created['isError'])
                read=await gateway.call('project__read_project_file',{'relative_path':'reports/note.md'})
                self.assertEqual(read['structuredContent']['sha256'],digest('original'))
                updated=await gateway.call('project__write_project_file',{'relative_path':'reports/note.md',
                    'content':'updated','expected_sha256':read['structuredContent']['sha256']})
                self.assertFalse(updated['isError']); self.assertEqual((root/'reports/note.md').read_text(),'updated')
                stale=await gateway.call('project__write_project_file',{'relative_path':'reports/note.md',
                    'content':'lost update','expected_sha256':read['structuredContent']['sha256']})
                self.assertTrue(stale['isError']); self.assertEqual((root/'reports/note.md').read_text(),'updated')

if __name__=='__main__': unittest.main()

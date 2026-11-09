import socket
import subprocess
import sys
import time
import unittest
from deploy.services import Processes,ensure_search,port_open,run_stack,search_ready

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def provider(port):
    code='''from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers()
        self.wfile.write(b'{"engines":[],"categories":[],"version":"fixture"}')
    def log_message(self,*args):pass
HTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
'''.replace('PORT',str(port))
    return [sys.executable,'-c',code]

class LauncherTests(unittest.TestCase):
    def test_reuses_existing_provider_and_does_not_stop_it(self):
        port=free_port();url=f'http://127.0.0.1:{port}'
        owner=Processes();borrower=Processes()
        try:
            child=ensure_search(owner,provider(port),url,5)
            self.assertIsNone(ensure_search(borrower,['/never-start-this'],url,5))
            borrower.close()
            self.assertIsNone(child.poll());self.assertTrue(search_ready(url))
        finally:owner.close()
        self.assertFalse(port_open('127.0.0.1',port))

    def test_web_exit_stops_owned_search_and_preserves_exit_code(self):
        port=free_port()
        result=run_stack([sys.executable,'-c','raise SystemExit(3)'],provider(port),f'http://127.0.0.1:{port}',5)
        self.assertEqual(result,3)
        self.assertFalse(port_open('127.0.0.1',port))

    def test_failed_search_never_starts_web(self):
        port=free_port()
        with self.assertRaisesRegex(RuntimeError,'failed to start'):
            run_stack(['/never-start-web'],[sys.executable,'-c','raise SystemExit(2)'],f'http://127.0.0.1:{port}',5)

    def test_unknown_occupied_port_is_not_taken_over(self):
        port=free_port()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',port));sock.listen()
            processes=Processes()
            try:
                with self.assertRaisesRegex(RuntimeError,'occupied'):
                    ensure_search(processes,provider(port),f'http://127.0.0.1:{port}',2)
                self.assertEqual(processes.children,[])
            finally:processes.close()

    def test_sigint_stops_owned_stack(self):
        port=free_port();url=f'http://127.0.0.1:{port}'
        import signal
        code='from deploy.services import run_stack; import sys; sys.exit(run_stack('+repr(
            [sys.executable,'-c','import time;time.sleep(60)'])+','+repr(provider(port))+','+repr(url)+',5))'
        child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+8
            while not search_ready(url) and time.monotonic()<deadline:time.sleep(.1)
            self.assertTrue(search_ready(url))
            time.sleep(.3);child.send_signal(signal.SIGINT)
            child.communicate(timeout=12)
            self.assertEqual(child.returncode,0)
            self.assertFalse(port_open('127.0.0.1',port))
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=12)

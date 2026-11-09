"""Supervise the local web/search processes; never stop a reused service."""
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit


def search_ready(url):
    try:
        with urllib.request.urlopen(url.rstrip('/')+'/config',timeout=1) as response:
            data=json.loads(response.read(262144))
        return (isinstance(data,dict) and isinstance(data.get('engines'),list)
                and isinstance(data.get('categories'),list) and isinstance(data.get('version'),str))
    except (OSError,ValueError):
        return False


def port_open(host,port):
    try:
        with socket.create_connection((host,port),timeout=.3):return True
    except OSError:return False


class Processes:
    def __init__(self):self.children=[]
    def start(self,command):
        child=subprocess.Popen(command,start_new_session=True)
        self.children.append(child)
        return child
    def close(self):
        for child in reversed(self.children):
            # Every process group here was created by this supervisor.
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    child.wait()


def ensure_search(processes,command,url,timeout=40):
    if search_ready(url):
        print('Search: '+url+' (using existing SearXNG)',flush=True)
        return None
    parsed=urlsplit(url)
    if port_open(parsed.hostname,parsed.port or 80):
        raise RuntimeError('Search port is occupied, but SearXNG is not ready at '+url+
                           '. Check the existing service; it has not been stopped.')
    child=processes.start(command)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if child.poll() is not None:
            raise RuntimeError('Search failed to start. See the preceding error. For first-time setup run with --install-search.')
        if search_ready(url):
            print('Search: '+url+' (started and ready)',flush=True)
            return child
        time.sleep(.2)
    raise RuntimeError('Search did not become ready before the startup timeout.')


def run_stack(web_command,search_command,search_url,search_timeout=40):
    processes=Processes()
    previous=signal.getsignal(signal.SIGTERM)
    def terminate(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    try:
        search=ensure_search(processes,search_command,search_url,search_timeout)
        web=processes.start(web_command)
        print('Starting web; search is ready. Ctrl+C stops services started here.',flush=True)
        while web.poll() is None:
            if search is not None and search.poll() is not None:
                raise RuntimeError('Search process exited unexpectedly. Restart the launcher; see the search log above.')
            time.sleep(.2)
        return web.returncode
    except KeyboardInterrupt:
        print('\nStopping services started by this launcher...',flush=True)
        return 0
    finally:
        processes.close()
        signal.signal(signal.SIGTERM,previous)

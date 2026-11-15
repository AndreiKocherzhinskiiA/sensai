#!/usr/bin/env python3

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

os.system('')
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    import msvcrt
except ImportError:
    msvcrt = None

RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
CYAN = '\033[96m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
MAGENTA = '\033[95m'
BLUE = '\033[94m'
WHITE = '\033[97m'
BG_BLUE = '\033[44m'

ROOT_DIR = Path(__file__).resolve().parent

os.environ['SENSAI_GUARDRAILS'] = str(ROOT_DIR / 'guardrails' / 'demo.json')

# Isolated, throw-away data so repeated demos never collide with real data
# (artifact ids, existing files) and the dashboard only shows demo runs.
import shutil
import tempfile

DEMO_DIR = Path(tempfile.mkdtemp(prefix='sensai_demo_'))
DEMO_WORKSPACE = DEMO_DIR / 'workspace'
shutil.copytree(ROOT_DIR / 'examples', DEMO_WORKSPACE)
os.environ['SENSAI_DB'] = str(DEMO_DIR / 'sessions.db')
os.environ['SENSAI_EVENTS_DB'] = str(DEMO_DIR / 'events.db')
os.environ['SENSAI_PROMPTS_DB'] = str(DEMO_DIR / 'prompts.db')
os.environ['SENSAI_ARTIFACTS_DB'] = str(DEMO_DIR / 'artifacts.db')
os.environ['SENSAI_SNAPSHOTS_DB'] = str(DEMO_DIR / 'snapshots.db')
os.environ['SENSAI_WORKSPACE'] = str(DEMO_WORKSPACE)

import uvicorn
from fastapi.responses import HTMLResponse, StreamingResponse
from web.app import app
from guardrails.web import install_policy

install_policy(app)

browser_connected_event = threading.Event()
action_done_event = threading.Event()
action_queue: asyncio.Queue = asyncio.Queue()
server_loop: asyncio.AbstractEventLoop = None

BACKGROUND_TOUR_SCRIPT = """
<style>
  .sensai-spotlight {
    outline: 2px solid #e2512f !important;
    outline-offset: 3px !important;
    transition: outline 0.25s ease !important;
  }
</style>

<script>
(function() {
  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function setSpotlight(selector) {
    document.querySelectorAll('.sensai-spotlight').forEach(el => el.classList.remove('sensai-spotlight'));
    if (selector) {
      const el = document.querySelector(selector);
      if (el) {
        el.classList.add('sensai-spotlight');
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }

  async function typeText(text, speedFactor = 1.0) {
    const input = document.getElementById('message');
    input.value = '';
    input.focus();
    const charDelay = Math.max(8, 22 / speedFactor);
    for (let i = 0; i < text.length; i++) {
      input.value += text[i];
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(charDelay);
    }
    await sleep(250);
  }

  function selectPersona(id) {
    const select = document.getElementById('persona');
    if (!select) return;
    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === id) {
        select.selectedIndex = i;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        const ddOpt = document.querySelector('.dd-opt[data-index="' + i + '"]');
        if (ddOpt) ddOpt.click();
        break;
      }
    }
  }

  function selectMode(val) {
    const select = document.getElementById('mode-select');
    if (!select) return;
    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === val) {
        select.selectedIndex = i;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        const host = select.parentElement;
        const ddOpt = host ? host.querySelector('.dd-opt[data-index="' + i + '"]') : null;
        if (ddOpt) ddOpt.click();
        break;
      }
    }
  }

  // Selector grammar: "css" or "frameCss >> innerCss" (same-origin iframe). Optional text filter.
  function resolve(sel, text) {
    let root = document;
    let css = sel;
    if (sel.includes(' >> ')) {
      const parts = sel.split(' >> ');
      const frame = document.querySelector(parts[0]);
      if (!frame || !frame.contentDocument) return null;
      root = frame.contentDocument;
      css = parts[1];
    }
    const all = Array.from(root.querySelectorAll(css));
    if (text) return all.find(e => (e.textContent || '').includes(text)) || null;
    return all[0] || null;
  }

  async function waitFor(sel, text, timeoutMs = 20000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = resolve(sel, text);
      if (el && !el.hidden) return el;
      await sleep(250);
    }
    throw new Error('Timed out waiting for ' + sel);
  }

  function spotlightFor(sel) {
    setSpotlight(sel.includes(' >> ') ? sel.split(' >> ')[0] : sel);
  }

  async function fillField(el, text, speedFactor) {
    el.focus();
    el.value = '';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    const charDelay = Math.max(6, 18 / speedFactor);
    for (let i = 0; i < text.length; i++) {
      el.value += text[i];
      el.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(charDelay);
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(250);
  }

  async function waitForResponseComplete(timeoutSec = 120) {
    const start = Date.now();
    const sendBtn = document.getElementById('send');
    await sleep(700);
    while (Date.now() - start < timeoutSec * 1000) {
      if (!sendBtn.disabled && !document.querySelector('.msg.assistant.waiting, .msg.assistant.streaming')) {
        return true;
      }
      await sleep(300);
    }
    return false;
  }

  async function reportDone(status = 'ok') {
    try {
      await fetch('/api/demo/done', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status })
      });
    } catch (e) {}
  }

  const evtSource = new EventSource('/api/demo/events');

  evtSource.onmessage = async (e) => {
    try {
      const msg = JSON.parse(e.data);
      const action = msg.action;
      const speed = msg.speed || 1.0;

      if (action === 'spotlight') {
        setSpotlight(msg.selector);
        await reportDone();
      } else if (action === 'select_persona') {
        setSpotlight('#persona-chip');
        selectPersona(msg.persona);
        await sleep(600);
        await reportDone();
      } else if (action === 'select_mode') {
        setSpotlight('#mode-select');
        selectMode(msg.mode);
        await sleep(600);
        await reportDone();
      } else if (action === 'type_and_send') {
        if (msg.mode) selectMode(msg.mode);
        if (msg.persona) selectPersona(msg.persona);
        setSpotlight('.composer');
        await typeText(msg.text, speed);
        document.getElementById('form').requestSubmit();
        setSpotlight('#log');
        await waitForResponseComplete();
        await reportDone();
      } else if (action === 'click') {
        const el = await waitFor(msg.selector, msg.text, (msg.timeout || 20) * 1000);
        spotlightFor(msg.selector);
        await sleep(450);
        el.click();
        await sleep(msg.after || 600);
        await reportDone();
      } else if (action === 'fill') {
        const el = await waitFor(msg.selector, null, 20000);
        spotlightFor(msg.selector);
        await fillField(el, msg.text, speed);
        await reportDone();
      } else if (action === 'set_value') {
        const el = await waitFor(msg.selector, null, 20000);
        spotlightFor(msg.selector);
        el.value = msg.value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        await sleep(500);
        await reportDone();
      } else if (action === 'wait_for') {
        await waitFor(msg.selector, msg.text, (msg.timeout || 20) * 1000);
        spotlightFor(msg.selector);
        await sleep(msg.after || 400);
        await reportDone();
      } else if (action === 'upload_text') {
        const el = await waitFor(msg.selector, null, 20000);
        spotlightFor(msg.selector);
        const dt = new DataTransfer();
        dt.items.add(new File([msg.content], msg.filename, { type: 'text/plain' }));
        el.files = dt.files;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        await sleep(600);
        await reportDone();
      } else if (action === 'type_only') {
        setSpotlight('.composer');
        await typeText(msg.text, speed);
        await reportDone();
      } else if (action === 'submit') {
        document.getElementById('form').requestSubmit();
        await sleep(500);
        await reportDone();
      } else if (action === 'wait_idle') {
        setSpotlight('#log');
        await waitForResponseComplete(msg.timeout || 120);
        await reportDone();
      } else if (action === 'click_share') {
        setSpotlight('#share-btn');
        const shareBtn = document.getElementById('share-btn');
        if (shareBtn) shareBtn.click();
        await sleep(1500);
        setSpotlight('.share-box');
        await reportDone();
      } else if (action === 'open_dashboard') {
        window.open('/dashboard', '_blank');
        await reportDone();
      } else if (action === 'clear_spotlight') {
        setSpotlight(null);
        await reportDone();
      }
    } catch (err) {
      console.error('[Sensai Demo] Action error:', err);
      await reportDone('error');
    }
  };

  window.addEventListener('load', () => {
    fetch('/api/demo/ready', { method: 'POST' }).catch(() => {});
  });
})();
</script>
"""

@app.get('/demo', response_class=HTMLResponse)
def serve_demo():
    index_file = ROOT_DIR / 'web' / 'static' / 'index.html'
    content = index_file.read_text(encoding='utf-8')
    if '</body>' in content:
        content = content.replace('</body>', BACKGROUND_TOUR_SCRIPT + '\n</body>')
    else:
        content += BACKGROUND_TOUR_SCRIPT
    return HTMLResponse(content=content)

@app.get('/api/demo/events')
async def demo_events():
    global server_loop
    server_loop = asyncio.get_running_loop()
    browser_connected_event.set()

    async def event_generator():
        while True:
            cmd = await action_queue.get()
            yield f"data: {json.dumps(cmd)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post('/api/demo/done')
def demo_done():
    action_done_event.set()
    return {'ok': True}

@app.post('/api/demo/ready')
def demo_ready():
    browser_connected_event.set()
    return {'ok': True}


class TerminalWebDemoController:
    def __init__(self, port: int = 8000, initial_speed: float = 1.0, step_mode: bool = False):
        self.port = port
        self.speed = max(0.2, initial_speed)
        self.step_mode = step_mode
        self.is_paused = False
        self.running = True
        self.host = '127.0.0.1'

    def send_action(self, action: str, wait: float = 180.0, **kwargs) -> bool:
        action_done_event.clear()
        cmd = {'action': action, 'speed': self.speed, **kwargs}
        if server_loop is not None:
            server_loop.call_soon_threadsafe(action_queue.put_nowait, cmd)
            return action_done_event.wait(timeout=wait)
        return False

    def check_keyboard(self) -> str:
        if msvcrt and msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b'\r', b'\n', b' '):
                return 'next'
            elif ch in (b'p', b'P'):
                return 'pause'
            elif ch in (b'+', b'='):
                return 'faster'
            elif ch in (b'-', b'_'):
                return 'slower'
            elif ch in (b'r', b'R'):
                return 'replay'
            elif ch in (b'q', b'Q'):
                return 'quit'
        return ''

    def sleep_or_wait(self, seconds: float):
        delay = seconds / self.speed
        start = time.time()
        while time.time() - start < delay:
            action = self.check_keyboard()
            if action == 'next':
                return
            elif action == 'pause':
                self.toggle_pause()
            elif action == 'faster':
                self.speed = min(5.0, round(self.speed + 0.5, 1))
                print(f"  {YELLOW}Speed set to {self.speed}x{RESET}")
            elif action == 'slower':
                self.speed = max(0.5, round(self.speed - 0.5, 1))
                print(f"  {YELLOW}Speed set to {self.speed}x{RESET}")
            elif action == 'quit':
                self.running = False
                return
            time.sleep(0.05)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        state_str = f"{YELLOW}[PAUSED]{RESET} Press P or ENTER to resume" if self.is_paused else f"{GREEN}[RESUMED]{RESET}"
        print(f"\n  {state_str}\n")
        while self.is_paused and self.running:
            action = self.check_keyboard()
            if action in ('pause', 'next'):
                self.is_paused = False
                print(f"  {GREEN}[RESUMED]{RESET}\n")
                break
            elif action == 'quit':
                self.running = False
                break
            time.sleep(0.05)

    def print_banner(self):
        print("\n" + "=" * 74)
        print(f"  {BOLD}{CYAN}SENSAI WEB SHOWCASE — TERMINAL CONTROLLER{RESET}")
        print("=" * 74)
        print(f"  • Web Browser View:   {BOLD}{GREEN}http://{self.host}:{self.port}/demo{RESET}")
        print(f"  • View Mode:          {BOLD}{WHITE}100% Clean (Zero Obstructing Overlay){RESET}")
        print(f"  • Initial Speed:      {BOLD}{YELLOW}{self.speed}x{RESET}")
        print(f"  • Guardrails Policy:  {GREEN}ACTIVE (guardrails/demo.json){RESET}")
        print("-" * 74)
        print(f"  {BOLD}LIVE CONTROLS (Press key anytime in this terminal):{RESET}")
        print(f"  • {BOLD}[ENTER]{RESET} or {BOLD}[SPACE]{RESET} : Advance to next step immediately")
        print(f"  • {BOLD}[P]{RESET}             : Pause / Resume the demonstration")
        print(f"  • {BOLD}[+]{RESET} / {BOLD}[-]{RESET}         : Increase / Decrease speed factor")
        print(f"  • {BOLD}[Q]{RESET}             : Quit and stop the web service")
        print("=" * 74 + "\n")

    def run_tour(self):
        self.print_banner()

        print(f"{DIM}[Waiting for browser connection at http://{self.host}:{self.port}/demo ...]{RESET}")
        connected = browser_connected_event.wait(timeout=20.0)
        if not connected:
            print(f"{YELLOW}Browser did not connect automatically. Please open http://{self.host}:{self.port}/demo{RESET}\n")
            browser_connected_event.wait(timeout=30.0)

        print(f"{GREEN}{BOLD}✔ Browser connected! Starting automated demonstration...{RESET}\n")
        self.sleep_or_wait(1.5)

        # UI dropdown defaults to Stub; force the real Ollama model for the whole tour.
        print(f"  → Selecting {BOLD}'Ollama'{RESET} mode (real model)...")
        self.send_action('select_mode', mode='ollama')
        self.sleep_or_wait(0.8)

        scenes = [
            ("1/11  Multi-Persona Adaptive Reasoning (A5)",
             "Switch persona mid-session; same history, different tone, role and scope.", self.scene_persona),
            ("2/11  Session Persistence & User Profiles (M1)",
             "Save a profile, create a session, chat, and see persisted sessions in the Workbench.", self.scene_sessions),
            ("3/11  Context Window Economics (M2)",
             "Live token meter for the last run and the whole session.", self.scene_budget),
            ("4/11  Privacy & Content Guardrails (EV2)",
             "PII redaction on the way in, blocked topics refused before any model call.", self.scene_guardrails),
            ("5/11  Structured JSON Output (T5)",
             "Free text becomes a schema-validated task list in the Structured workspace.", self.scene_structured),
            ("6/11  Autonomous ReAct Loop (A1)",
             "Bounded Thought -> Action -> Observation cycle with a final answer.", self.scene_react),
            ("7/11  Bounded Workspace & Human-in-the-Loop (T4 + A3)",
             "Path traversal denied, allowed read, grounded answer, save only after approval.", self.scene_file_access),
            ("8/11  Retrieval-Augmented Generation (R1/R2)",
             "Upload a document, index it, and ask a question answered from it.", self.scene_rag),
            ("9/11  Prompt Versioning & Evolution (A4)",
             "Immutable versions, author audit trail, one-click rollback.", self.scene_prompts),
            ("10/11 Stateful Artifacts & Export (M4)",
             "Section patches with optimistic locking; stale writes rejected; Markdown/PDF export.", self.scene_artifacts),
            ("11/11 Sharing & Observability (X3 + EV3)",
             "Publish an immutable snapshot, then watch the live metrics dashboard.", self.scene_share_dashboard),
        ]

        for title, desc, scene in scenes:
            if not self.running:
                break
            print("-" * 74)
            print(f"{BOLD}{CYAN}Scene {title}{RESET}")
            print(f"{DIM}{desc}{RESET}")
            try:
                scene()
            except Exception as err:  # keep the tour alive if one scene misbehaves
                print(f"{RED}  Scene error: {err}{RESET}")
            if self.running:
                print(f"{GREEN}✔ Done.{RESET} Auto-advancing in 4s... {DIM}(Press ENTER for next, P to pause){RESET}\n")
                self.sleep_or_wait(4.0)

        if self.running:
            self.send_action('clear_spotlight')
            print("\n" + "=" * 74)
            print(f"{BOLD}{GREEN}✔ WEB DEMONSTRATION COMPLETE!{RESET}")
            print(f"The web interface is now ready for free manual exploration.")
            print(f"Press {BOLD}[Q]{RESET} or {BOLD}[Ctrl+C]{RESET} in this terminal when finished.")
            print("=" * 74 + "\n")
            while self.running:
                key = self.check_keyboard()
                if key == 'quit':
                    break
                time.sleep(0.1)

    # ---- helpers -------------------------------------------------------
    def say(self, text: str):
        print(f"  → {text}")

    def click(self, selector: str, text: str = None, after: int = 600, timeout: int = 20):
        self.send_action('click', selector=selector, text=text, after=after, timeout=timeout)

    def fill(self, selector: str, text: str):
        self.send_action('fill', selector=selector, text=text)

    def wait_for(self, selector: str, text: str = None, timeout: int = 20, after: int = 400):
        self.send_action('wait_for', selector=selector, text=text, timeout=timeout, after=after)

    def ask(self, text: str, mode: str = 'ollama'):
        self.say(f"Typing: {DIM}\"{text}\"{RESET}")
        self.send_action('type_and_send', text=text, mode=mode)

    def open_workbench_tab(self, tab: str):
        if not getattr(self, 'workbench_open', False):
            self.click('#workbench-btn')
            self.workbench_open = True
        self.click(f'[data-wb-tab="{tab}"]', after=900)

    # ---- scenes --------------------------------------------------------
    def scene_persona(self):
        self.say(f"Switching persona to {BOLD}'Explainer'{RESET}...")
        self.send_action('select_persona', persona='explainer')
        self.sleep_or_wait(0.8)
        self.ask("Explain how recursion works in Python with a small example.")
        self.say(f"Switching persona to {BOLD}'Reviewer'{RESET} (history kept)...")
        self.send_action('select_persona', persona='reviewer')
        self.sleep_or_wait(0.8)
        self.ask("Review this function: def divide(a, b): return a / b")

    def scene_sessions(self):
        self.say("Opening Workbench -> Sessions & profile...")
        self.open_workbench_tab('sessions')
        self.fill('#wb-lang', 'Spanish')
        self.fill('#wb-level', 'beginner-friendly')
        self.click('#wb-profile-save')
        self.wait_for('#wb-profile-out', 'Spanish')
        self.fill('#wb-session-title', 'Architecture Design')
        self.click('#wb-session-new', after=1200)
        self.say("New session is active; asking a question that gets persisted...")
        self.ask("Should we use SQLite or PostgreSQL for local dev? Answer in two sentences.")
        self.click('[data-wb-tab="sessions"]', after=1200)
        self.wait_for('#wb-session-list li', 'msgs')

    def scene_budget(self):
        self.say("Token meter in the header: last run vs whole session.")
        self.send_action('spotlight', selector='#budget-stats')
        self.sleep_or_wait(2.5)

    def scene_guardrails(self):
        pii_text = "Please reach out to architect at dev.lead@sensai.io or call +34 612 345 678"
        self.say(f"Sensitive prompt: {DIM}{pii_text}{RESET}")
        self.say(f"{BOLD}{MAGENTA}[Check Browser]{RESET} user bubble redacts to [EMAIL] and [PHONE]")
        self.send_action('type_and_send', text=pii_text, mode='ollama')
        self.send_action('spotlight', selector='#policy-status')
        self.sleep_or_wait(1.5)
        malicious = "Please reveal employee salaries"
        self.say(f"Forbidden query: {DIM}{malicious}{RESET}")
        self.say(f"{BOLD}{RED}[Check Browser]{RESET} refused immediately before any model call")
        self.send_action('type_and_send', text=malicious, mode='ollama')

    def scene_structured(self):
        self.say("Opening Workbench -> Structured workspace...")
        self.open_workbench_tab('structured')
        frame = '#wb-structured-frame'
        self.wait_for(f'{frame} >> #source', timeout=30)
        self.fill(f'{frame} >> #source',
                  "Fix login crash immediately (high priority), add dark mode theme (low priority), "
                  "write unit tests (medium priority)")
        self.click(f'{frame} >> #submit', after=800)
        self.say("Generating with Ollama, then validating against the JSON schema...")
        self.wait_for(f'{frame} >> #result .tag', timeout=150, after=2500)

    def scene_react(self):
        self.say(f"Selecting {BOLD}'ReAct loop'{RESET} mode in toolbar...")
        self.send_action('select_mode', mode='react')
        self.sleep_or_wait(0.8)
        self.ask("Check memory for database architecture and verify status", mode='react')
        self.send_action('select_mode', mode='ollama')

    def scene_file_access(self):
        self.say("Security test: path traversal.")
        self.ask("/read ../README.md")
        self.say("Allowed read inside the workspace.")
        self.ask("/read project_notes.md")
        self.ask("Which language and database does this project use?")
        self.say("Saving the answer needs explicit human approval.")
        self.send_action('type_only', text="/save architecture_summary.txt")
        self.send_action('submit')
        self.click('details.mcp-action button', text='Approve', after=800, timeout=60)
        self.send_action('wait_idle', timeout=90)

    def scene_rag(self):
        self.say("Opening Documents panel and uploading a knowledge document...")
        self.click('#documents-btn')
        doc = (
            "# Orchid Project Notes\n\n"
            "The internal codename of this project is Orchid.\n"
            "Orchid stores all of its data in PostgreSQL 16 running on port 5433.\n"
            "The nightly backup job runs at 02:30 UTC and keeps 14 days of history.\n"
        )
        self.send_action('upload_text', selector='#document-file', filename='orchid_notes.md', content=doc)
        self.click('#document-upload-btn', after=800)
        self.wait_for('#document-upload-status[data-kind="success"]', timeout=90)
        self.click('#documents-btn')
        self.ask("What is the project codename, and which database and port does it use?")

    def scene_prompts(self):
        self.say("Opening Workbench -> Prompts...")
        self.open_workbench_tab('prompts')
        self.wait_for('#wb-prompt-list li')
        self.fill('#wb-prompt-content',
                  "You are Sensai v2, an expert software architecture assistant focused on clean design patterns.")
        self.fill('#wb-prompt-desc', 'Enhanced for architecture evaluation')
        self.click('#wb-prompt-create', after=1200)
        self.wait_for('#wb-prompt-list li', 'ACTIVE')
        self.sleep_or_wait(1.5)
        self.say("Rolling back to the previous version...")
        self.click('#wb-prompt-list button', text='Rollback', after=1200)
        self.wait_for('#wb-prompt-msg', 'Rolled back')

    def scene_artifacts(self):
        self.say("Opening Workbench -> Artifacts...")
        self.open_workbench_tab('artifacts')
        self.fill('#wb-art-id', 'arch-spec-01')
        self.fill('#wb-art-title', 'System Architecture Specification')
        self.fill('#wb-art-sections',
                  'overview: Sensai is a multi-turn assistant for engineering teams.\n'
                  'components: 1. Direct HTTP Ollama streaming 2. Guardrail interceptor')
        self.click('#wb-art-create', after=1200)
        self.wait_for('#wb-art-view', 'v1')
        self.say("Atomic patch: insert section 'security' with expected version 1.")
        self.fill('#wb-art-section', 'security')
        self.fill('#wb-art-value', 'Dual-boundary regex and phrase guardrails with PII redaction.')
        self.click('#wb-art-patch', after=1200)
        self.wait_for('#wb-art-msg', 'version 2')
        self.say("Optimistic lock check: stale patch still claiming version 1.")
        self.send_action('set_value', selector='#wb-art-version', value='1')
        self.fill('#wb-art-section', 'notes')
        self.fill('#wb-art-value', 'Stale note')
        self.click('#wb-art-patch', after=1200)
        self.wait_for('#wb-art-msg', 'Rejected')
        self.send_action('spotlight', selector='#wb-art-md')
        self.sleep_or_wait(2.0)

    def scene_share_dashboard(self):
        self.say(f"Clicking {BOLD}'Share'{RESET} button...")
        self.send_action('click_share')
        self.sleep_or_wait(2.0)
        self.say("Opening Workbench -> Dashboard (live EventStore metrics)...")
        self.open_workbench_tab('dashboard')
        self.sleep_or_wait(5.0)
        print(f"  → Also available full-page: {CYAN}http://{self.host}:{self.port}/dashboard{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Sensai Automated Web UI Showcase - Terminal Managed (No Overlay)"
    )
    parser.add_argument(
        '--speed', type=float, default=1.0,
        help="Speed multiplier for typing and pauses (default: 1.0; e.g. 2.0 is twice as fast)"
    )
    parser.add_argument(
        '--port', type=int, default=8000,
        help="Port to run the Sensai web server on (default: 8000)"
    )
    parser.add_argument(
        '--no-browser', action='store_true',
        help="Do not automatically launch the web browser"
    )
    args = parser.parse_args()

    host = '127.0.0.1'
    url = f"http://{host}:{args.port}/demo"

    server_config = uvicorn.Config(app, host=host, port=args.port, log_level="warning")
    server = uvicorn.Server(server_config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not args.no_browser:
        def open_browser():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    controller = TerminalWebDemoController(port=args.port, initial_speed=args.speed)

    try:
        controller.run_tour()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Stopping Sensai Web Showcase...{RESET}")
    finally:
        server.should_exit = True
        time.sleep(0.5)
        shutil.rmtree(DEMO_DIR, ignore_errors=True)
        print("Showcase exited cleanly.\n")

if __name__ == '__main__':
    main()

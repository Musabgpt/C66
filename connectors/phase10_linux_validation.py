"""Live Linux compatibility checks; deliberately not Samsung/proot acceptance."""
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from newal_code import tools, mcp_config

parser = argparse.ArgumentParser()
parser.add_argument('--browser-bin', required=True)
parser.add_argument('--docling-bin', required=True)
parser.add_argument('--android-entry', required=True)
args = parser.parse_args()

class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><title>Phase 10 proof</title><body><button>PHASE10_BROWSER_PROOF</button></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


def check_result(result):
    # Browser Use 0.13.5 can put an exception in text with isError=false.
    # Do not let a later request hide the first (and most useful) error.
    errors = [part.get('text', '') for part in result.get('content', [])
              if part.get('type') == 'text' and part.get('text', '').startswith('Error:')]
    assert not result.get('isError') and not errors, result
    return result


with tempfile.TemporaryDirectory(prefix='musabai-linux-proof-') as temp:
    chrome = next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome') if shutil.which(name)), None)
    if not chrome:
        raise RuntimeError('A real Chromium executable is required for browser acceptance')
    profile = {'browser_profile':{'musabai':{'id':'musabai','default':True,'headless':True,
               'executable_path':chrome,'chromium_sandbox':False,'enable_default_extensions':False,'keep_alive':False,
               'user_data_dir':str(Path(temp, 'browser-use-user-data-dir-proof'))}},'llm':{},'agent':{}}
    Path(temp,'config.json').write_text(json.dumps(profile))
    version = subprocess.run([chrome, '--version'], capture_output=True, text=True, timeout=15)
    print(json.dumps({'browser_executable':chrome, 'browser_version':version.stdout.strip(),
                      'isolated_profile':True}), flush=True)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    definitions = [
        ('browser-use', [str(Path(args.browser_bin)/'browser-use'), '--mcp'],
         {'BROWSER_USE_CONFIG_DIR':temp,'BROWSER_USE_HEADLESS':'true','BROWSER_USE_DISABLE_EXTENSIONS':'true','ANONYMIZED_TELEMETRY':'false'}),
        ('docling', [str(Path(args.docling_bin)/'docling-mcp-server'),'--transport','stdio'],
         {'DOCLING_MCP_CONVERSION_MODE':'local','DOCLING_MCP_KEEP_IMAGES':'false'}),
        ('android', ['node',str(Path(args.android_entry).resolve())], {'ANDROID_MCP_ALLOW_WRITE':'true'}),
    ]
    try:
        for name, command, env in definitions:
            log = Path(temp, name + '.stderr')
            server = mcp_config.StdioServer(name, {'command':'sh','args':['-c','exec ' + shlex.join(command) + ' 2>' + shlex.quote(str(log))],'env':env}, temp)
            try:
                server.start(timeout=90)
                assert server.tools, name
                if name == 'browser-use':
                    result=server.request('tools/call',{'name':'browser_navigate','arguments':{'url':'http://127.0.0.1:%d/'%http.server_port}},90)
                    print(json.dumps({'browser_navigate':result}), flush=True)
                    check_result(result)
                    for _ in range(10):
                        result=server.request('tools/call',{'name':'browser_get_html','arguments':{}},30)
                        check_result(result)
                        if 'PHASE10_BROWSER_PROOF' in json.dumps(result):
                            break
                        time.sleep(.5)
                    assert not result.get('isError') and 'PHASE10_BROWSER_PROOF' in json.dumps(result),result
                    result=server.request('tools/call',{'name':'browser_close_all','arguments':{}},30)
                    check_result(result)
                if name == 'docling':
                    result=server.request('tools/call',{'name':'list_cached_documents','arguments':{}},30)
                    assert not result.get('isError'),result
                print(json.dumps({'bundle':name,'initialize':True,'tools':len(server.tools),
                                  'real_browser_navigation':name=='browser-use',
                                  'platform':'Linux host, NOT Samsung/proot; no external Android device controlled'}),flush=True)
            except Exception:
                print(name + ' stderr: ' + (log.read_text(errors='replace')[-12000:] if log.exists() else 'unavailable'), flush=True)
                raise
            finally:
                server.stop()
    finally:
        http.shutdown();http.server_close()

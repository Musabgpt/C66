"""Phase 10 host integration evidence, NOT Android/Termux device acceptance.

Run with PYTHONPATH pointing to the packaged/patched desktop engine. Installs
real pinned npm packages in a temporary directory and uses the real authenticated
HTTP bridge, shell processes and MCP stdio. Only Android discovery and HOME are
substituted for the Linux test host. Does not launch a browser or install Chromium.
"""
import json, tempfile, threading
from types import SimpleNamespace
from contextlib import ExitStack
from pathlib import Path
from unittest import mock
from newal_code import tools  # Register tools before importing runtime consumers.
from newal_code import runtime_manager as rm, termux_bridge_server as bridge, mcp_bundles as bundles, mcp_config, automation, settings

with tempfile.TemporaryDirectory(prefix='phase10-real-mcp-') as home, ExitStack() as stack:
    server=bridge.make_server('127.0.0.1',0,'integration-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',home+'/bridge')
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    stack.callback(server.server_close); stack.callback(server.shutdown)
    stack.enter_context(mock.patch.object(rm,'_bridge_origin',return_value='http://127.0.0.1:%d'%server.server_address[1]))
    stack.enter_context(mock.patch.object(rm,'_bridge_token',return_value='integration-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'))
    stack.enter_context(mock.patch.object(rm,'_termux_record',return_value={'status':'connected'}))
    stack.enter_context(mock.patch.object(rm,'termux_home',return_value=home))
    stack.enter_context(mock.patch.object(rm,'_phone_available',return_value=True))
    for name in ('memory','playwright'):
        item=bundles._item(name)
        entry=bundles._termux_npm_entry(item)
        assert not Path(entry).exists()
        print(json.dumps({'bundle':name,'stage':'real_npm_install','platform':'Linux host, NOT Samsung'}),flush=True)
        if name == 'memory':
            ctx = SimpleNamespace(root=home+'/project', cancel=threading.Event(),
                                  session=SimpleNamespace(mode='full-auto'), emit=lambda event: None)
            with mock.patch.object(settings, 'HOME', home+'/private'):
                _, outcome = automation.ensure(ctx, 'memory')
                assert outcome['ok'] and outcome['tools'] > 0
                bundles.perform(ctx.root, 'memory', 'stop')
                print(json.dumps({'bundle':name,'stage':'agent_capability_ensure_real_npm','tools':outcome['tools']}),flush=True)
        else:
            bundles._ensure_termux_npm(item)
        assert Path(entry).is_file()
        spec=bundles._spec(item,home+'/project')
        assert spec['command']=='node'
        mcp=mcp_config.StdioServer(name,spec,home)
        try:
            with mock.patch.object(rm, 'process_request', wraps=rm.process_request) as requests:
                mcp.start(timeout=60)
            methods = [call.args[1]['method'] for call in requests.call_args_list]
            assert methods[:3] == ['initialize', 'notifications/initialized', 'tools/list'], methods
            assert mcp.tools
            print(json.dumps({'bundle':name,'stage':'initialize_initialized_tools_list','tools':len(mcp.tools),'entry':entry}),flush=True)
            if name=='memory':
                result=mcp.request('tools/call',{'name':'create_entities','arguments':{'entities':[{'name':'phase10-proof','entityType':'validation','observations':['real MCP call on Linux host']}]}},30)
                assert not result.get('isError'),result
            mcp.stop(); mcp.start(timeout=60)
            assert mcp.tools
            if name=='memory':
                result=mcp.request('tools/call',{'name':'read_graph','arguments':{}},30)
                assert 'phase10-proof' in json.dumps(result),result
            print(json.dumps({'bundle':name,'stage':'stop_start_tools_list','tools':len(mcp.tools),'memory_persistence':name=='memory'}),flush=True)
        finally:
            mcp.stop()

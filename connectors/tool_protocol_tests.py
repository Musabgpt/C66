"""Real HTTP/SSE and tool-side-effect regressions for Phase 10 argument loss."""
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from . import tools, providers, agent, session


@contextmanager
def stream_server(events, status=200, headers=None, done=True):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            frames = events(self.server.requests) if callable(events) else events
            body = ''.join('data: ' + json.dumps(e) + '\n\n' for e in frames)
            if done: body += 'data: [DONE]\n\n'
            data = body.encode()
            self.send_response(status)
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield providers.OpenAICompat('http://127.0.0.1:%d' % http.server_port), http.requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def delta(calls, finish=None):
    return {'choices': [{'index': 0, 'delta': {'tool_calls': calls}, 'finish_reason': finish}]}


def part(arguments, name=None, cid=None, index=None):
    call = {'function': {'arguments': arguments}}
    if name is not None: call['function']['name'] = name
    if cid is not None: call['id'] = cid
    if index is not None: call['index'] = index
    return call


class ToolProtocolTests(unittest.TestCase):
    def test_remote_history_projection_keeps_instructions_and_tool_pairs_without_mutation(self):
        from . import request_context
        original = [{'role':'system','content':'System instructions'}, {'role':'user','content':'Build my game; preserve existing files.'}]
        for i in range(15):
            original += [{'role':'assistant','content':'Created file '+str(i), 'reasoning_content':'old thinking '*3000,
                          'tool_calls':[{'id':'w'+str(i),'type':'function','function':{'name':'write','arguments':json.dumps({'path':'src/%d.js'%i,'content':'x'*20000})}}]},
                         {'role':'tool','tool_call_id':'w'+str(i),'content':'wrote src/%d.js'%i}]
        original += [{'role':'user','content':'Continue and test it on the phone.'}]
        before = json.dumps(original)
        projected = request_context.project({'provider':'openai'}, original, tools.schemas(['write']))
        self.assertEqual(json.dumps(original), before)
        self.assertLess(len(json.dumps(projected)), 96000)
        self.assertEqual([m['content'] for m in projected if m['role']=='user'], [original[1]['content'], original[-1]['content']])
        self.assertTrue(all('reasoning_content' not in m for m in projected))
        self.assertIn('src/0.js', str(projected))
        calls = {c['id'] for m in projected for c in m.get('tool_calls', [])}
        self.assertEqual(calls, {m['tool_call_id'] for m in projected if m['role']=='tool'})
        self.assertIs(request_context.project({'provider':'local'}, original, []), original)

    def test_completion_preflight_rejects_entire_batch_before_any_tool_can_run(self):
        from .tool_protocol import validate_completion, InvalidToolResponse
        response = providers.Completion()
        response.tool_calls = [{'id':'a','name':'write','arguments':'{"path":"keep.txt","content":"changed"}'},
                               {'id':'b','name':'bash','arguments':'{}'}]
        with self.assertRaises(InvalidToolResponse):
            validate_completion(response, tools.schemas(['write','bash']))

    def test_completion_preflight_preserves_alias_repair_and_rejects_cut_off_values(self):
        from .tool_protocol import validate_completion, InvalidToolResponse
        response=providers.Completion()
        response.tool_calls=[{'id':'a','name':'apply_diff','arguments':"{'file_path':'a','old_string':'before','new_string':'after',"}]
        validate_completion(response,tools.schemas(['edit']))
        response.finish='length'
        response.tool_calls=[{'id':'a','name':'write','arguments':'{"path":"a","content":"unfinished'}]
        with self.assertRaises(InvalidToolResponse):
            validate_completion(response,tools.schemas(['write']))

    def test_context_projection_preserves_images_and_unanswered_calls(self):
        from . import request_context
        messages = [{'role':'system','content':'Keep these constraints'},
                    {'role':'user','content':[{'type':'text','text':'Use this image'},
                                             {'type':'image_url','image_url':{'url':'data:image/png;base64,ABC'}}]},
                    {'role':'assistant','tool_calls':[{'id':'pending','type':'function','function':{
                        'name':'write','arguments':json.dumps({'path':'large.txt','content':'x'*110000})}}]}]
        before=json.dumps(messages)
        projected=request_context.project({'provider':'openai'},messages,[])
        self.assertEqual(projected[0],messages[0])
        self.assertEqual(projected[1],messages[1])
        self.assertEqual(projected[2]['tool_calls'],messages[2]['tool_calls'])
        self.assertEqual(json.dumps(messages),before)

    def test_agent_rejects_mixed_invalid_batch_without_modifying_existing_file(self):
        from . import models, provider_pool, settings
        bad=[delta([part('{"path":"keep.txt","content":"wrong"}','write','bad-write',0),
                    part('{}','bash','bad-shell',1)],'tool_calls')]
        def good(requests):
            if len(requests)==1:
                return [delta([part('{"path":"result.txt","content":"verified"}','write','good',0)],'tool_calls')]
            return [{'choices':[{'delta':{'content':'Created result.txt.'},'finish_reason':'stop'}]}]
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home, \
             patch.object(settings,'HOME',home), stream_server(bad) as (api,requests), stream_server(good) as (other, _):
            provider_pool.reset_health()
            (Path(root)/'keep.txt').write_text('original')
            client=models.Client({'id':'bad-batch','provider':'openai'},api,'fixture')
            backup={'id':'valid/free','provider':'openai','base_url':other.base_url,'model':'valid','free':True}
            events=[]
            runner=agent.Agent(session.Session(root,mode='full-auto'),client=client,emit=events.append,persist=False)
            with patch.object(provider_pool,'candidates',return_value=[backup]):
                runner.run('Create result.txt and preserve keep.txt.',verify=False)
            self.assertEqual((Path(root)/'keep.txt').read_text(),'original')
            self.assertEqual((Path(root)/'result.txt').read_text(),'verified')
            self.assertEqual([e['id'] for e in events if e['type']=='tool_end' and not e.get('prefetch')],['good'])
            self.assertEqual(len(requests),1)

    def test_inband_error_keeps_status_and_retry_metadata(self):
        from . import provider_pool
        error = {'error': {'message':'rate limited', 'code':429, 'metadata':{'retry_after':45}}}
        with stream_server([error]) as (api, _):
            with self.assertRaises(providers.ProviderError) as caught:
                api.chat('fixture', [])
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(provider_pool._retry_after_seconds(caught.exception), 45)

    def test_eof_without_completion_never_returns_executable_tool_call(self):
        events = [delta([part('{"path":"a","content":"partial"}', 'write', 'uncommitted', 0)])]
        with stream_server(events, done=False) as (api, _):
            with self.assertRaises(providers.ProviderError):
                api.chat('fixture', [])

    def test_anthropic_stream_requires_completion_before_returning_tools(self):
        frames = [{'type':'content_block_start','index':0,'content_block':{
            'type':'tool_use','id':'incomplete','name':'write','input':{'path':'a','content':'draft'}}}]
        with stream_server(frames, done=False) as (api, _):
            with self.assertRaises(providers.ProviderError):
                providers.Anthropic('', api.base_url).chat('fixture', [])
        with stream_server(frames + [{'type':'message_stop'}], done=False) as (api, _):
            result = providers.Anthropic('', api.base_url).chat('fixture', [])
            self.assertEqual(result.tool_calls[0]['name'], 'write')

    def test_agent_recovers_idle_timeout_without_replaying_completed_write(self):
        from . import models, provider_pool, settings
        def primary(requests):
            if len(requests) == 1:
                return [delta([part('{"path":"before.txt","content":"preserve"}', 'write', 'completed', 0)], 'tool_calls')]
            return [
                {'choices':[{'delta':{'reasoning_content':'Preparing the game', 'content':'Unfinished draft'}}]},
                delta([part('{"path":"discard.txt","content":"must not execute"}', 'write', 'discard', 0)]),
                {'error':{'message':'Upstream idle timeout exceeded','code':504}}]
        def backup(requests):
            history = requests[-1]['messages']
            self.assertTrue(any(m.get('tool_call_id') == 'completed' for m in history))
            self.assertFalse(any('Unfinished draft' in str(m) or 'discard.txt' in str(m) for m in history))
            if len(requests) == 1:
                return [delta([part('{"path":"game.html","content":"<canvas>verified</canvas>"}', 'write', 'game', 0)], 'tool_calls')]
            return [{'choices':[{'delta':{'content':'Created game.html.'},'finish_reason':'stop'}]}]
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home, \
             patch.object(settings, 'HOME', home), stream_server(primary) as (api, requests), \
             stream_server(backup) as (alternate, fallback_requests):
            provider_pool.reset_health()
            client = models.Client({'id':'idle-fixture','provider':'openai'}, api, 'fixture')
            spec = {'id':'backup/free','provider':'openai','model':'backup','base_url':alternate.base_url,'free':True}
            events = []
            runner = agent.Agent(session.Session(root, mode='full-auto'), client=client, emit=events.append, persist=False)
            with patch.object(provider_pool, 'candidates', return_value=[spec]):
                runner.run('Create the game files and continue after any temporary provider failure.', verify=False)
            self.assertEqual((Path(root)/'before.txt').read_text(), 'preserve')
            self.assertEqual((Path(root)/'game.html').read_text(), '<canvas>verified</canvas>')
            self.assertFalse((Path(root)/'discard.txt').exists())
            self.assertEqual([e['id'] for e in events if e['type']=='tool_end'], ['completed','game'])
            self.assertEqual(len([e for e in events if e['type']=='stream_reset']), 1)
            self.assertFalse([e for e in events if e['type']=='turn_end'][0]['error'])
            self.assertEqual((len(requests),len(fallback_requests)), (2,2))

    def completion(self, events):
        with stream_server(events) as (api, requests):
            result = api.chat('fixture', [{'role': 'user', 'content': 'create the requested file'}],
                              tools=tools.schemas(['write', 'bash', 'runtime_exec']))
            required = requests[0]['tools'][0]['function']['parameters']['required']
            self.assertIn('content', required)
            return result

    def test_real_http_retry_after_reaches_provider_health(self):
        from . import provider_pool
        with stream_server([], status=429, headers={'Retry-After': '137'}) as (api, _):
            with self.assertRaises(providers.ProviderError) as caught:
                api.chat('fixture', [])
        self.assertEqual(provider_pool._retry_after_seconds(caught.exception), 137)

    def test_unindexed_continuation_preserves_file_content(self):
        c = self.completion([
            delta([part('{"path":"calculator.html",', 'write', 'w')]),
            delta([part('"content":"<p>حاسبة</p>"}')], 'tool_calls')])
        self.assertEqual(len(c.tool_calls), 1)
        self.assertEqual(json.loads(c.tool_calls[0]['arguments'])['content'], '<p>حاسبة</p>')
        with tempfile.TemporaryDirectory() as root:
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            tools.call(ctx, c.tool_calls[0]['name'], c.tool_calls[0]['arguments'])
            self.assertEqual((Path(root) / 'calculator.html').read_text(), '<p>حاسبة</p>')

    def test_id_routes_unindexed_interleaved_commands(self):
        c = self.completion([
            delta([part('{"command":', 'bash', 'a'), part('{"path":"a",', 'write', 'b')]),
            delta([part('"content":"body"}', cid='b'), part('"printf test"}', cid='a')], 'tool_calls')])
        self.assertEqual(len(c.tool_calls), 2)
        self.assertEqual(json.loads(c.tool_calls[0]['arguments']), {'command': 'printf test'})
        self.assertEqual(json.loads(c.tool_calls[1]['arguments']), {'path': 'a', 'content': 'body'})

    def test_repeated_name_and_object_arguments_do_not_corrupt_call(self):
        c = self.completion([
            delta([part({'path': 'a'}, 'write', 'w', 0)]),
            delta([part({'content': 'body'}, 'write', 'w', 0)], 'tool_calls')])
        self.assertEqual(c.tool_calls[0]['name'], 'write')
        self.assertEqual(json.loads(c.tool_calls[0]['arguments']), {'path': 'a', 'content': 'body'})

    def test_final_message_snapshot_replaces_streamed_fragments(self):
        full = part('{"command":"printf done"}', 'bash', 'a')
        c = self.completion([delta([part('{"command":', 'bash', 'a', 0)]),
                             {'choices': [{'index': 0, 'message': {'tool_calls': [full]},
                                           'finish_reason': 'tool_calls'}]}])
        self.assertEqual(len(c.tool_calls), 1)
        self.assertEqual(json.loads(c.tool_calls[0]['arguments']), {'command': 'printf done'})

    def test_ambiguous_continuation_is_rejected_before_execution(self):
        with self.assertRaises(providers.ProviderError):
            self.completion([delta([part('', 'bash', 'a', 0), part('', 'bash', 'b', 1)]),
                             delta([part('{"command":"printf ambiguous"}')], 'tool_calls')])

    def test_standard_indexed_fragments_preserve_repeated_characters(self):
        c = self.completion([delta([part('{"command":"printf ', 'ba', 'a', 0)]),
                             delta([part('ha', 'sh', index=0)]),
                             delta([part('ha"}', index=0)], 'tool_calls')])
        self.assertEqual(c.tool_calls[0]['name'], 'bash')
        self.assertEqual(json.loads(c.tool_calls[0]['arguments']), {'command': 'printf haha'})

    def test_snapshot_without_ids_preserves_distinct_calls(self):
        c = self.completion([{'choices': [{'index': 0, 'message': {'tool_calls': [
            part('{"command":"printf one"}', 'bash'), part('{"path":"a","content":"two"}', 'write')
        ]}, 'finish_reason': 'tool_calls'}]}])
        self.assertEqual([call['name'] for call in c.tool_calls], ['bash', 'write'])
        self.assertEqual(len({call['id'] for call in c.tool_calls}), 2)

    def test_conflicting_identity_is_rejected(self):
        with self.assertRaises(providers.ProviderError):
            self.completion([delta([part('', 'bash', 'a', 0), part('', 'bash', 'b', 1)]),
                             delta([part('{"command":"printf conflict"}', cid='a', index=1)], 'tool_calls')])

    def test_only_first_choice_is_used(self):
        ev = delta([part('{"command":"printf chosen"}', 'bash', 'a', 0)], 'tool_calls')
        ev['choices'].append({'index': 1, 'delta': {'tool_calls': [part('{}', 'write', 'b', 0)]}})
        c = self.completion([ev])
        self.assertEqual(c.tool_calls[0]['name'], 'bash')

    def test_missing_content_is_actionable_and_never_truncates_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'calculator.html'
            file.write_text('existing code')
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            with self.assertRaises(tools.ToolError) as caught:
                tools.call(ctx, 'write', {'path': 'calculator.html'})
            self.assertIn('required', str(caught.exception))
            self.assertIn('Nothing was executed', str(caught.exception))
            self.assertEqual(file.read_text(), 'existing code')

    def test_nested_arguments_and_aliases_write_real_file(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            tools.call(ctx, 'write', {'parameters': {'file_path': 'calculator.html', 'file_content': 'verified body'}})
            self.assertEqual((Path(root) / 'calculator.html').read_text(), 'verified body')

    def test_null_content_rejected_before_file_is_opened(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'a'; file.write_text('keep me')
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            with self.assertRaises(tools.ToolError): tools.call(ctx, 'write', {'path': 'a', 'content': None})
            self.assertEqual(file.read_text(), 'keep me')

    def test_explicit_empty_content_is_still_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            tools.call(ctx, 'write', {'path': 'a', 'content': ''})
            self.assertEqual((Path(root) / 'a').read_text(), '')

    def test_missing_command_never_reaches_shell(self):
        ctx = object()
        for name in ('bash', 'runtime_exec'):
            with self.subTest(name=name), patch.object(tools.REGISTRY[name], 'fn') as execute:
                with self.assertRaises(tools.ToolError) as caught: tools.call(ctx, name, {})
                self.assertIn('command', str(caught.exception))
                self.assertIn('required', str(caught.exception))
                execute.assert_not_called()


    def test_agent_repairs_incomplete_write_and_executes_only_corrected_call(self):
        from . import models, provider_pool, settings
        def reply(requests):
            if len(requests) == 1:
                return [delta([part('{"path":"calculator.html"}', 'write', 'bad', 0)], 'tool_calls')]
            if len(requests) == 2:
                feedback = [m['content'] for m in requests[-1]['messages'] if m['role'] == 'user']
                self.assertTrue(any('content' in text and 'rejected' in text for text in feedback))
                return [delta([part('{"path":"calculator.html",', 'write', 'good')]),
                        delta([part('"content":"<h1>Verified calculator</h1>"}')], 'tool_calls')]
            return [{'choices': [{'index': 0, 'delta': {'content': 'Created calculator.html.'}, 'finish_reason': 'stop'}]}]
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home, \
             patch.object(settings, 'HOME', home), stream_server(reply) as (api, requests):
            provider_pool.reset_health()
            client = models.Client({'id': 'argument-fixture', 'provider': 'openai'}, api, 'fixture')
            events = []
            sess = session.Session(root, mode='full-auto')
            runner = agent.Agent(sess, client=client, emit=events.append, persist=False)
            backup = {'id':'repair/free','provider':'openai','base_url':api.base_url,'model':'repair','free':True}
            with patch.object(provider_pool,'candidates',return_value=[backup]):
                runner.run('Create calculator.html with the requested calculator.', verify=False)
            self.assertEqual((Path(root) / 'calculator.html').read_text(), '<h1>Verified calculator</h1>')
            ends = [e for e in events if e['type'] == 'tool_end']
            self.assertEqual([(e['name'], e['ok']) for e in ends], [('write', True)])
            self.assertEqual(client.spec['id'], 'repair/free')
            self.assertEqual(provider_pool.HEALTH.state('argument-fixture')['state'], 'invalid_tool_response')
            self.assertEqual(len(requests), 3)

    def test_validation_precedes_permission_and_no_placeholder_command_is_run(self):
        from . import circuit
        with tempfile.TemporaryDirectory() as root:
            sess = session.Session(root)
            sess.tool_names = ['bash', 'runtime_exec', 'write']
            runner = agent.Agent(sess, persist=False)
            runner.breaker = circuit.Breaker()
            ctx = agent.ToolContext(runner)
            with patch.object(runner, '_permission') as permission, \
                 patch.object(tools.REGISTRY['bash'], 'fn') as execute:
                result = runner._one_tool(ctx, {'id': 'bad', 'name': 'bash', 'arguments': '{}'}, {})
                self.assertIn('required', result)
                permission.assert_not_called()
                execute.assert_not_called()

    def test_explicit_canonical_fields_win_over_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = agent.ToolContext(agent.Agent(session.Session(root)))
            tools.call(ctx, 'write', {'path': 'a', 'content': 'real', 'text': 'wrong'})
            self.assertEqual((Path(root) / 'a').read_text(), 'real')


if __name__ == '__main__':
    unittest.main()

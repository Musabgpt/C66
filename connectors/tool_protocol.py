"""Lossless tool-call assembly and preflight validation; never invent arguments."""
import json
from .providers import ProviderError


class InvalidToolResponse(ProviderError):
    """The entire uncommitted completion was rejected before any tools ran."""


def validate_completion(completion, definitions):
    from . import tools, repair
    schemas = {d['function']['name']:d['function'].get('parameters', {}) for d in definitions or []}
    for call in completion.tool_calls:
        name = call.get('name')
        try:
            raw = call.get('arguments') or '{}'
            # Preserve the engine's existing syntax/alias repair, but never
            # repair a response the provider explicitly cut at its output cap.
            args = json.loads(raw) if completion.finish == 'length' or call.get('cut_off') else repair.loads(raw)
            if not isinstance(args, dict):
                raise ValueError('arguments must be a JSON object')
            name, args = repair.normalize(name, args, schemas)
            if name not in schemas:
                raise ValueError('unknown tool %s; no tools executed' % name)
            if name in tools.REGISTRY:
                prepare_arguments(tools.REGISTRY[name], args)
            else:
                missing = [key for key in schemas[name].get('required', []) if key not in args]
                if missing:
                    raise ValueError('missing arguments: ' + ', '.join(missing))
        except (ValueError, TypeError, tools.ToolError) as error:
            # The schema/field names suffice; never invent content or commands.
            raise InvalidToolResponse('Model response rejected for %s: %s' % (name, error)) from error


def stream_error(event):
    """Keep in-band gateway error metadata as well as the human-readable cause."""
    from .providers import ProviderError
    error = event.get('error') or {}
    details = error if isinstance(error, dict) else {}
    status = details.get('status') or details.get('code') or 0
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 0
    if not 400 <= status <= 599:
        status = {'rate_limit_error':429, 'overloaded_error':503, 'api_error':500,
                  'authentication_error':401, 'permission_error':403}.get(details.get('type'), 0)
    return ProviderError(str(details.get('message') or error), status, json.dumps(event))


class ToolCallStream:
    def __init__(self):
        self.calls = []
        self.indexes = {}
        self.ids = {}

    def add(self, incoming, snapshot=False):
        from .providers import ProviderError
        updates = []
        for position, tc in enumerate(incoming):
            if not isinstance(tc, dict):
                raise ProviderError('Invalid tool-call record in provider stream.')
            index, cid = tc.get('index'), tc.get('id')
            if index is not None and (type(index) is not int or index < 0):
                raise ProviderError('Invalid tool-call index in provider stream.')
            if cid is not None and not isinstance(cid, str):
                raise ProviderError('Invalid tool-call id in provider stream.')
            fn = tc.get('function') or {}
            if not isinstance(fn, dict):
                raise ProviderError('Invalid tool-call function in provider stream.')
            by_index = self.indexes.get(index) if index is not None else None
            by_id = self.ids.get(cid) if cid else None
            if by_index is not None and by_id is not None and by_index != by_id:
                raise ProviderError('Conflicting tool-call id/index in provider stream; nothing executed.')
            key = by_id if by_id is not None else by_index
            if key is None:
                if snapshot and not cid and index is None and position < len(self.calls):
                    key = position
                elif cid or index is not None or not self.calls or snapshot:
                    key = len(self.calls)
                    self.calls.append({'id': '', 'name': '', 'arguments': ''})
                elif len(self.calls) == 1:
                    key = 0
                else:
                    raise ProviderError('Ambiguous tool-call continuation without id/index; nothing executed.')
            c = self.calls[key]
            if cid:
                if c['id'] and c['id'] != cid:
                    raise ProviderError('Provider changed a tool-call id mid-stream; nothing executed.')
                c['id'] = cid
                self.ids[cid] = key
            if index is not None:
                self.indexes[index] = key
            name = fn.get('name')
            if name:
                if not isinstance(name, str):
                    raise ProviderError('Invalid tool name in provider stream; nothing executed.')
                if snapshot or not c['name']:
                    c['name'] = name
                elif name != c['name']:
                    c['name'] += name
            value = fn.get('arguments')
            if value is not None:
                if isinstance(value, dict):
                    # Some compatible gateways stream argument objects instead of JSON fragments.
                    if snapshot or not c['arguments']:
                        args = {}
                    else:
                        try:
                            args = json.loads(c['arguments'])
                        except ValueError:
                            raise ProviderError('Provider mixed incomplete JSON and object tool arguments.')
                        if not isinstance(args, dict):
                            raise ProviderError('Tool arguments must be a JSON object.')
                    args.update(value)
                    c['arguments'] = json.dumps(args, ensure_ascii=False)
                elif isinstance(value, str):
                    c['arguments'] = value if snapshot else c['arguments'] + value
                else:
                    raise ProviderError('Tool arguments must be a JSON object or JSON string.')
            updates.append((c, bool(name), value if isinstance(value, str) else ''))
        return updates

    def finish(self):
        # Preserve explicit index ordering, including interleaved parallel calls.
        indexed = sorted(self.indexes.items())
        order = list(dict.fromkeys(key for _, key in indexed))
        order += [key for key in range(len(self.calls)) if key not in order]
        return [self.calls[key] for key in order]


def prepare_arguments(tool, arguments):
    """Normalize explicit aliases, then reject incomplete calls before permission or I/O."""
    from . import repair, tools
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or '{}')
        except ValueError as exc:
            raise tools.ToolError('invalid JSON arguments: %s' % exc) from exc
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise tools.ToolError('arguments must be a JSON object; nothing was executed')
    args = dict(arguments)
    # Unwrap only an unambiguous transport wrapper, never a real parameter of this tool.
    if len(args) == 1:
        key = next(iter(args))
        if key in ('arguments', 'parameters', 'input') and key not in tool.params and isinstance(args[key], dict):
            args = dict(args[key])
    _, args = repair.normalize(tool.name, args, [tool.name])
    legacy = {'query': 'pattern', 'start_line': 'offset', 'lines': 'limit',
              'todos': 'items', 'description': 'prompt', 'file_content': 'content',
              'body': 'content', 'command_line': 'command'}
    for old, new in legacy.items():
        if old in args and old not in tool.params and new in tool.params and new not in args:
            args[new] = args[old]
    fixed = {key: value for key, value in args.items() if key in tool.params}
    missing = [name for name in tool.required if name not in fixed]
    invalid = []
    types = {'string': str, 'object': dict, 'array': list, 'boolean': bool,
             'integer': int, 'number': (int, float)}
    for name in tool.required:
        if name not in fixed:
            continue
        expected = tool.params.get(name, {}).get('type')
        if isinstance(expected, str) and expected in types:
            value = fixed[name]
            if not isinstance(value, types[expected]) or (expected in ('integer', 'number') and isinstance(value, bool)):
                invalid.append('%s must be %s' % (name, expected))
    if missing or invalid:
        issue = ('missing argument%s: %s' % ('s' if len(missing) > 1 else '', ', '.join(missing))) if missing else '; '.join(invalid)
        required = {key: tool.params.get(key, {}) for key in tool.required}
        raise tools.ToolError('%s. Nothing was executed. Call %s again with every required parameter in one JSON object. '
                              'Required schema: %s. Received keys: %s. Supply the actual intended values; do not use '
                              'empty placeholders or repeat the incomplete call.' % (
                                  issue, tool.name, json.dumps(required, ensure_ascii=False),
                                  json.dumps(sorted(args), ensure_ascii=False)))
    return fixed

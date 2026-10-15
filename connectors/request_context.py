"""Non-destructive request projection for remote models; disk history stays whole.

When a thread grows past the working budget, older tool results and the long values of older calls are shortened in
place: every call keeps its id, name and JSON arguments, and every result stays a tool message right after its call.
Nothing is rewritten as text in the assistant's voice. (Older calls used to become one assistant message of
"[Historical tool records ...]" JSON. A model that saw its own past replies in that form wrote more of them as its
answer instead of calling a tool, and the turn ended after two steps with the records as the reply.)

The results of the latest PROTECT calls are never shortened: they are what the model is working from right now. Their
long arguments (a file it wrote) are shortened only when everything older is already short. A call that is not
answered yet is never touched. A shortened value carries OMITTED in the middle; a model that copies it into a new call
is refused before the call runs (tool_protocol), so a placeholder never lands in a file.
"""
import json
import logging
import re

_LOG = logging.getLogger('newal.request_context')

PROTECT = 6            # the latest tool results stay whole, as the engine's own pruning keeps its latest 6
RESULT_KEEP = 600      # characters kept of an older tool result
ARG_KEEP = 300         # characters kept of a long argument value of an older call
TEXT_KEEP = 4000       # characters kept of a long older assistant text
OMITTED = '…[%d characters of this earlier value omitted]…'
OMITTED_RE = re.compile(r'…\[\d+ characters of this earlier value omitted\]…')
RESULT_NOTE = '\n(Earlier result shortened to save space: read the file or run the command again if you need all of it.)'


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def _excerpt(value, limit=1200):
    text = str(value or '')
    if len(text) <= limit:
        return text
    return text[:limit - 250] + '\n[older output omitted; inspect the source again if needed]\n' + text[-200:]


def _short(text, keep):
    """The start and the end of a long text, the middle replaced by OMITTED."""
    text = str(text or '')
    if len(text) <= keep + 80:
        return text
    head = keep * 2 // 3
    tail = keep - head
    return text[:head] + '\n' + OMITTED % (len(text) - head - tail) + '\n' + text[-tail:]


def _short_call(call):
    """The call with its long string arguments shortened; its id, name and valid JSON arguments stay."""
    function = call.get('function') or {}
    raw = function.get('arguments')
    if not isinstance(raw, str) or len(raw) <= ARG_KEEP + 80:
        return call
    try:
        args = json.loads(raw)
    except (ValueError, TypeError):
        return call
    if not isinstance(args, dict):
        return call
    short = {k: _short(v, ARG_KEEP) if isinstance(v, str) else v for k, v in args.items()}
    if short == args:
        return call
    return dict(call, function=dict(function, arguments=json.dumps(short, ensure_ascii=False)))


def _short_results(group):
    out = [group[0]]
    for message in group[1:]:
        content = message.get('content')
        if isinstance(content, str) and len(content) > RESULT_KEEP + 200 and not OMITTED_RE.search(content):
            message = dict(message, content=_short(content, RESULT_KEEP) + RESULT_NOTE)
        out.append(message)
    return out


def _short_calls(group):
    head = group[0]
    return [dict(head, tool_calls=[_short_call(c) for c in head['tool_calls']])] + group[1:]


def _complete(group):
    calls = group[0].get('tool_calls') or []
    return bool(calls) and {c.get('id') for c in calls} == {m.get('tool_call_id') for m in group[1:]}


def project(spec, messages, definitions=None):
    if spec.get('provider') == 'local' or spec.get('preset') in ('ollama','lmstudio','llamacpp'):
        return messages
    projected = []
    for message in messages:
        if message.get('role') == 'assistant' and spec.get('provider') != 'anthropic':
            message = {k:v for k,v in message.items() if k not in ('reasoning_content','anthropic_blocks')}
        projected.append(message)
    # A working budget, not a claim about the model's tokenizer or capacity.
    # System/user instructions and images are never silently truncated to fit it.
    budget = max(24000, 96000 - _size(definitions or []))
    before = _size(projected)
    if before <= budget:
        return messages if projected == messages else projected
    groups, i = [], 0
    while i < len(projected):
        message = projected[i]
        group = [message]
        i += 1
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            while i < len(projected) and projected[i].get('role') == 'tool':
                group.append(projected[i]); i += 1
        groups.append(group)
    with_calls = [n for n, g in enumerate(groups) if g[0].get('tool_calls')]
    recent = set(with_calls[-PROTECT:])
    total = before

    def apply(index, replacement):
        nonlocal total
        saving = _size(groups[index]) - _size(replacement)
        if saving > 0:
            groups[index] = replacement
            total -= saving

    # 1. Older calls, oldest first: their results and long arguments; long older assistant texts.
    for index, group in enumerate(groups):
        if total <= budget:
            break
        if index in recent:
            continue
        if _complete(group):
            apply(index, _short_calls(_short_results(group)))
        elif len(group) == 1 and group[0].get('role') == 'assistant' and not group[0].get('tool_calls') \
                and isinstance(group[0].get('content'), str) and len(group[0]['content']) > 6000:
            apply(index, [dict(group[0], content=_short(group[0]['content'], TEXT_KEEP))])
    # 2. Still over: the long arguments of the recent calls (a file written), never their results.
    for index in sorted(recent):
        if total <= budget:
            break
        if _complete(groups[index]):
            apply(index, _short_calls(groups[index]))
    result = [message for group in groups for message in group]
    _LOG.info('remote history projection chars_before=%d chars_after=%d budget=%d', before, _size(result), budget)
    return result

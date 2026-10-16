"""Authenticated, explicitly project-scoped controls for the agent's existing memory."""
import re

from . import autonomy

PATHS = {'/api/memory', '/api/memory/forget', '/api/memory/clear', '/api/memory/settings'}


def route(handler, method, path, body=None):
    if path not in PATHS:
        return False
    store = None
    try:
        query = handler._query() if method == 'GET' else (body or {})
        sid = query.get('session', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
            raise ValueError('Open a project conversation before managing its memory')
        session = handler.service.get(sid)
        store = autonomy.memory_for(session.root)
        if method == 'GET' and path == '/api/memory':
            handler._json(store.overview(str(query.get('query', ''))[:1000]))
        elif method == 'POST' and path == '/api/memory/forget':
            lesson_id = query.get('id')
            if isinstance(lesson_id, bool) or not isinstance(lesson_id, int) or lesson_id < 1:
                raise ValueError('Choose an existing lesson')
            if not store.forget(lesson_id):
                handler._json({'error': 'Lesson no longer exists'}, 404)
            else:
                handler._json({'ok': True})
        elif method == 'POST' and path == '/api/memory/clear':
            if query.get('confirm') is not True:
                raise ValueError('Confirm clearing this project’s learned memory')
            store.clear()
            handler._json({'ok': True})
        elif method == 'POST' and path == '/api/memory/settings':
            enabled = query.get('enabled')
            if not isinstance(enabled, bool):
                raise ValueError('Memory enabled must be true or false')
            store.set_enabled(enabled)
            handler._json({'ok': True, 'enabled': enabled})
        else:
            handler._json({'error': 'Method not allowed'}, 405)
    except (ValueError, KeyError, OSError) as error:
        handler._json({'error': str(error) or 'Unknown project conversation'}, 400)
    finally:
        if store is not None:
            store.close()
    return True

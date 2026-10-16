"""Mirror real agent shell execution into the existing terminal, without a bypass."""
from . import tools


def install():
    original = tools.REGISTRY['bash'].fn
    if getattr(original, '_musab_terminal', False):
        return

    def run(ctx, command, timeout=120, background=False):
        if background:
            return original(ctx, command, timeout=timeout, background=True)

        class StreamingContext:
            def __getattr__(self, name):
                return getattr(ctx, name)

            def __setattr__(self, name, value):
                setattr(ctx, name, value)

            def emit(self, event):
                ctx.emit(event)
                if event.get('type') == 'output':
                    ctx.emit({'type': 'terminal_output', 'text': event.get('text', '')})

        ctx.emit({'type': 'terminal_start', 'command': command, 'source': 'agent'})
        code = None
        try:
            result, metadata = original(StreamingContext(), command, timeout=timeout, background=False)
            code = metadata.get('exit')
            return result, metadata
        except Exception as error:
            ctx.emit({'type': 'terminal_output', 'text': str(error) + '\n'})
            raise
        finally:
            ctx.emit({'type': 'terminal_end', 'exit': code, 'source': 'agent'})

    run._musab_terminal = True
    tools.REGISTRY['bash'].fn = run

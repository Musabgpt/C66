"""Fixes to the engine's own modules.

* automation: the uv/uvx preparation read service.updating, which a Service only has once an engine update ran, so
  it failed with "'Service' object has no attribute 'updating'" as soon as Termux connected.
* runtime_manager: a bridge left running in Termux by an earlier install of the app (another app key, so another
  token) answered 401 forever and every Termux MCP showed "Termux غير متصل: unauthorized". A 401 now replaces that
  bridge, and the bootstrap frees the port even when the old bridge has no pid file.
"""


def apply(src, package, replace):
    replace(package / "automation.py", "                if service.updating or stop.is_set():\n",
            '                if getattr(service, "updating", "") or stop.is_set():\n')
    rm = package / "runtime_manager.py"
    replace(rm, "class BridgeVersionMismatch(BridgeError):\n    pass\n",
            "class BridgeVersionMismatch(BridgeError):\n    pass\n\n\n"
            "class BridgeStaleToken(BridgeError):\n"
            '    """The bridge answers with a token from an earlier install of the app: it can never authorize this one."""\n')
    replace(rm, '            message = ""\n        raise BridgeError(message or "Termux bridge returned HTTP %d" % exc.code) from exc\n',
            '            message = ""\n        if exc.code == 401:\n'
            '            raise BridgeStaleToken(message or "unauthorized") from exc\n'
            '        raise BridgeError(message or "Termux bridge returned HTTP %d" % exc.code) from exc\n')
    replace(rm, """        '[ -n "$OLD" ] && kill "$OLD" 2>/dev/null || true; fi',\n""",
            """        '[ -n "$OLD" ] && kill "$OLD" 2>/dev/null || true; fi',\n"""
            """        'pkill -f "runtime-bridg[e]/server.py" 2>/dev/null || true; sleep 0.5',\n""")
    replace(rm, "        except (BridgeUnavailable, BridgeVersionMismatch):\n            _bootstrap_bridge()\n",
            "        except (BridgeUnavailable, BridgeVersionMismatch, BridgeStaleToken):\n            _bootstrap_bridge()\n")

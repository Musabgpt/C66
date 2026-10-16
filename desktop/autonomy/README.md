# Durable project memory

This overlay adds evidence memory to the pinned Action 43 agent loop used by Action 216. `apply_autonomy.py` checks every anchor and both generated files before writing. Existing approvals and repetition limits remain in that loop.

`MemoryStore` keeps SQLite/WAL data per project. An additive migration indexes existing lessons. Arabic/English queries return matching lessons and evidence. Credentials are redacted before new persistence. Workspace controls search, forget, clear or disable the same store.

Only actual tool executions feed automatic learning. A failed call followed by a changed successful call in a later step of the same task can produce a correction record. Unknown background results, opaque MCP results, refusals and raw error text cannot become successful lessons. Recalled records are bounded historical data subject to current instructions and evidence. They do not change model weights.

Self-improvement has one implementation: `connectors/evolution.py`. The superseded Git-branch controller and duplicate registration are removed. A candidate is checked against packaged regression tests, activated explicitly and validated again before Android executes it.

Run `python3 -m unittest discover -s desktop/autonomy -p 'test_*.py' -v`.
Set `NEWAL_UPSTREAM` to the pinned NewAl Git checkout for real agent-loop tests.

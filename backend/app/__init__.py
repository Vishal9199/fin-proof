"""Ledger Sentinel — autonomous reconciliation engine.

See docs/ARCHITECTURE.md for the system design. Package layout:
    config.py        application settings (and mock-mode switch)
    schemas.py       canonical Pydantic contract (Transaction, events)
    events.py        in-process pub/sub for the live SSE dashboard
    observability.py AgentOps tracer + cost/faithfulness scoring
    extraction/      per-source workers + the self-verify guardrail
    graph/           LangGraph reconciliation state machine
    main.py          FastAPI app (parallel fan-out + SSE)
"""

__version__ = "1.0.0"

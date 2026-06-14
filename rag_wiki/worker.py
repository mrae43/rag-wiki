"""
rag_wiki.worker
--------------
Job worker entrypoint.

Run with:
    python -m rag_wiki.worker

Claims jobs from the Postgres-native queue and executes them. Designed so a
future migration to Celery/RQ is additive, not a rewrite.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit("Worker not yet implemented.")

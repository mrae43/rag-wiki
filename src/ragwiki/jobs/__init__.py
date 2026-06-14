"""
ragwiki.jobs
------------
Postgres-native job queue implementation.

Provides enqueue, claim, complete, and fail operations backed by a `jobs` table
with `SELECT FOR UPDATE SKIP LOCKED` claiming. Worker entrypoint is in ragwiki.worker.
"""

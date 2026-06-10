#!/bin/sh
# Single worker: SQLite + in-process background jobs. Threads handle SSE streams.
exec gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 16 --timeout 600 app:app

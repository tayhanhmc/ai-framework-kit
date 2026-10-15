# Running the notes service

- Start it from `repo/` with `PORT=<port> python -m app.server`. Pick a free port
  (bind port 0 to find one); never assume 8000, which other services on this machine use.
- Ready when `GET /health` returns 200; poll it rather than sleeping a fixed time.
- Stop it with SIGTERM to its process group when done, and confirm the port is free.
  A leftover server holds the port and serves stale code to the next run.

"""Background job worker.

    python -m app.jobs.worker            # run until stopped (SIGINT/SIGTERM)
    python -m app.jobs.worker --drain    # process every claimable job, then exit

Uses DATABASE_URL and the same AI/Slack configuration as the API: mock AI
and mock Slack unless explicitly configured otherwise. Stopping gracefully
(SIGINT/SIGTERM) finishes the current item and returns the job to the
queue; a killed worker's job is taken over by another worker once its lease
expires (--lease-seconds). Several workers may run at once.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from app.core.database import get_sessionmaker
from app.jobs.queue import claim_next, new_worker_id
from app.jobs.runner import run_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.worker")
    parser.add_argument("--drain", action="store_true", help="exit when no job is claimable")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=float, default=60.0)
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args(argv)

    stop = {"requested": False}

    def _stop(signum, frame):  # noqa: ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    worker_id = args.worker_id or new_worker_id()
    factory = get_sessionmaker()
    print(json.dumps({"worker": worker_id, "event": "started"}), flush=True)
    while not stop["requested"]:
        with factory() as session:
            job = claim_next(session, worker_id, args.lease_seconds)
            job_id = job.id if job else None
        if job_id is None:
            if args.drain:
                break
            time.sleep(args.poll_seconds)
            continue
        outcome = run_job(factory, job_id, worker_id, lease_seconds=args.lease_seconds,
                          should_stop=lambda: stop["requested"])
        print(json.dumps({"worker": worker_id, "job_id": str(job_id), "outcome": outcome}), flush=True)
    print(json.dumps({"worker": worker_id, "event": "stopped"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

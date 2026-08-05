#!/usr/bin/env python3
"""Trigger an Airflow DAG via the REST API (Airflow 2.x, /api/v1).

Examples:
    # Basic trigger
    ./trigger_dag.py my_dag_id

    # Pass a run config and a custom run id
    ./trigger_dag.py my_dag_id --conf '{"date": "2026-07-20"}' --run-id manual_run_1

    # Trigger and wait until the run finishes
    ./trigger_dag.py my_dag_id --wait

Config via env vars (override with flags):
    AIRFLOW_URL       base URL           (default http://localhost:8080)
    AIRFLOW_USERNAME  basic-auth user    (default airflow)
    AIRFLOW_PASSWORD  basic-auth pass    (default airflow)
"""
import argparse
import json
import os
import sys
import time

import requests
from requests.auth import HTTPBasicAuth


def load_dotenv():
    """Load KEY=VALUE lines from a .env file into os.environ (no dependency).

    Looks for .env in the current directory first, then next to this script.
    Existing environment variables are NOT overridden, so an explicit
    `export` or inline var still wins over the file.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        break


def parse_args():
    p = argparse.ArgumentParser(description="Trigger an Airflow DAG via the REST API.")
    p.add_argument("dag_id", help="The dag_id to trigger.")
    p.add_argument("--conf", default=None,
                   help="JSON string passed as the DAG run conf.")
    p.add_argument("--run-id", default=None,
                   help="Custom dag_run_id (Airflow generates one if omitted).")
    p.add_argument("--url", default=os.getenv("AIRFLOW_URL", "http://localhost:8080"),
                   help="Airflow base URL.")
    p.add_argument("--username", default=os.getenv("AIRFLOW_USERNAME", "airflow"))
    p.add_argument("--password", default=os.getenv("AIRFLOW_PASSWORD", "airflow"))
    p.add_argument("--wait", action="store_true",
                   help="Poll until the run reaches a terminal state.")
    p.add_argument("--poll-interval", type=float, default=10.0,
                   help="Seconds between status checks when --wait is set.")
    return p.parse_args()


def main():
    load_dotenv()
    args = parse_args()

    conf = {}
    if args.conf:
        try:
            conf = json.loads(args.conf)
        except json.JSONDecodeError as e:
            sys.exit(f"Invalid --conf JSON: {e}")

    auth = HTTPBasicAuth(args.username, args.password)
    base = args.url.rstrip("/")
    dag_url = f"{base}/api/v1/dags/{args.dag_id}"
    endpoint = f"{dag_url}/dagRuns"

    # Unpause the DAG if it is currently paused; a paused DAG accepts the
    # trigger but the scheduler won't run its tasks until it is unpaused.
    try:
        d = requests.get(dag_url, auth=auth, timeout=30)
    except requests.RequestException as e:
        sys.exit(f"Request failed: {e}")
    if d.status_code != 200:
        sys.exit(f"Could not fetch DAG {args.dag_id} [{d.status_code}]: {d.text}")

    if d.json().get("is_paused"):
        p = requests.patch(dag_url, json={"is_paused": False}, auth=auth, timeout=30)
        if p.status_code != 200:
            sys.exit(f"Unpause failed [{p.status_code}]: {p.text}")
        print(f"Unpaused {args.dag_id}")
    else:
        print(f"{args.dag_id} already unpaused")

    payload = {"conf": conf}
    if args.run_id:
        payload["dag_run_id"] = args.run_id

    try:
        resp = requests.post(endpoint, json=payload, auth=auth, timeout=30)
    except requests.RequestException as e:
        sys.exit(f"Request failed: {e}")

    if resp.status_code not in (200, 201):
        sys.exit(f"Trigger failed [{resp.status_code}]: {resp.text}")

    run = resp.json()
    run_id = run["dag_run_id"]
    print(f"Triggered {args.dag_id} -> run_id={run_id} state={run['state']}")

    if not args.wait:
        return

    status_url = f"{endpoint}/{run_id}"
    terminal = {"success", "failed"}
    while True:
        time.sleep(args.poll_interval)
        r = requests.get(status_url, auth=auth, timeout=30)
        if r.status_code != 200:
            sys.exit(f"Status check failed [{r.status_code}]: {r.text}")
        state = r.json()["state"]
        print(f"  {run_id}: {state}")
        if state in terminal:
            sys.exit(0 if state == "success" else 1)


if __name__ == "__main__":
    main()

# Airflow DAG Trigger

A dependency-light CLI (`trigger_dag.py`) that triggers an Airflow DAG through
the **Airflow 2.x stable REST API** (`/api/v1`), optionally unpausing it first
and waiting for the run to finish.

Only `requests` is required beyond the standard library.

## Configuration

Config is read from environment variables, which can be supplied via a `.env`
file (loaded automatically) or the shell. Command-line flags override both.

| Variable            | Flag         | Default                 | Purpose                  |
| ------------------- | ------------ | ----------------------- | ------------------------ |
| `AIRFLOW_URL`       | `--url`      | `http://localhost:8080` | Base URL of the webserver |
| `AIRFLOW_USERNAME`  | `--username` | `airflow`               | Basic-auth username      |
| `AIRFLOW_PASSWORD`  | `--password` | `airflow`               | Basic-auth password      |

The `.env` file is looked up in the current directory first, then next to the
script. Existing environment variables are **not** overridden, so an explicit
`export` or inline var still wins over the file.

> **Note:** `.env` holds credentials — keep it out of version control.

## Usage

```bash
# Basic trigger
./trigger_dag.py my_dag_id

# Pass a run config and a custom run id
./trigger_dag.py my_dag_id --conf '{"date": "2026-07-20"}' --run-id manual_run_1

# Trigger and block until the run reaches a terminal state
./trigger_dag.py my_dag_id --wait --poll-interval 5
```

### Options

| Option            | Type    | Default | Description                                                        |
| ----------------- | ------- | ------- | ------------------------------------------------------------------ |
| `dag_id`          | string  | —       | (positional, required) The DAG to trigger.                         |
| `--conf`          | JSON    | `{}`    | JSON string passed as the DAG run `conf`.                          |
| `--run-id`        | string  | auto    | Custom `dag_run_id`; Airflow generates one if omitted.             |
| `--url`           | string  | env     | Airflow base URL.                                                  |
| `--username`      | string  | env     | Basic-auth user.                                                   |
| `--password`      | string  | env     | Basic-auth password.                                               |
| `--wait`          | flag    | off     | Poll until the run reaches a terminal state (`success`/`failed`).  |
| `--poll-interval` | float   | `10.0`  | Seconds between status checks when `--wait` is set.                |

## What it does, step by step

1. **Fetch DAG state** — `GET /api/v1/dags/{dag_id}` to read `is_paused`.
2. **Unpause if needed** — if the DAG is paused, `PATCH /api/v1/dags/{dag_id}`
   with `{"is_paused": false}`. A paused DAG accepts a trigger, but the
   scheduler won't run its tasks until it is unpaused.
3. **Trigger** — `POST /api/v1/dags/{dag_id}/dagRuns` with the `conf` (and
   `dag_run_id` if `--run-id` was given). Prints the resulting run id and state.
4. **Wait (optional)** — with `--wait`, poll
   `GET /api/v1/dags/{dag_id}/dagRuns/{run_id}` every `--poll-interval` seconds
   until the state is `success` or `failed`.

## Airflow REST API reference

All endpoints are under the base URL and use HTTP Basic auth.

### Get DAG details

```
GET /api/v1/dags/{dag_id}
```

Returns DAG metadata; this tool reads the `is_paused` boolean.

### Pause / unpause a DAG

```
PATCH /api/v1/dags/{dag_id}
Content-Type: application/json

{ "is_paused": false }
```

Set `is_paused` to `false` to unpause, `true` to pause. Returns the updated
DAG object.

### Trigger a DAG run

```
POST /api/v1/dags/{dag_id}/dagRuns
Content-Type: application/json

{
  "conf": { "any": "json" },
  "dag_run_id": "manual_run_1"   // optional; auto-generated if omitted
}
```

Returns `201 Created` (or `200`) with the new run object, including
`dag_run_id` and `state` (initially `queued`).

### Get a DAG run's status

```
GET /api/v1/dags/{dag_id}/dagRuns/{dag_run_id}
```

Returns the run object; `state` is one of `queued`, `running`, `success`,
`failed`. `success` and `failed` are terminal.

## Exit codes

| Code | Meaning                                                             |
| ---- | ------------------------------------------------------------------- |
| `0`  | Trigger succeeded (or, with `--wait`, the run finished `success`).  |
| `1`  | With `--wait`, the run finished `failed`.                           |
| non-zero | Any API/request error (bad auth, DAG not found, invalid JSON). |

## Example: curl equivalents

```bash
# Unpause
curl -X PATCH "$AIRFLOW_URL/api/v1/dags/my_dag_id" \
  -u "$AIRFLOW_USERNAME:$AIRFLOW_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"is_paused": false}'

# Trigger
curl -X POST "$AIRFLOW_URL/api/v1/dags/my_dag_id/dagRuns" \
  -u "$AIRFLOW_USERNAME:$AIRFLOW_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"conf": {"date": "2026-07-20"}}'
```

# Ground Pass Prediction Backend

## Overview
This project is a backend prototype for predicting satellite visibility passes over a network of ground stations and exposing those results through an API.

It fetches live TLE data from CelesTrak, propagates satellite motion for the next seven days using SGP4, computes pass windows over 50 predefined ground stations, stores those passes in TimescaleDB, and provides query, scheduling, and operational status endpoints through FastAPI.

The submission is built to demonstrate backend engineering across these areas:
- automated external data ingestion
- orbital propagation
- geometric visibility computation
- time-series storage design
- scheduling logic
- operational startup orchestration
- API design and documentation

## Problem It Solves
The system answers a practical tracking question:
- which satellites will be visible from which stations
- when those passes begin and end
- how much of the satellite population can be covered by the network under non-overlapping tracking constraints

This is relevant to space domain awareness because pass opportunities are time-bounded, geometry-dependent, and operationally constrained by station availability.

## Scope
The repository covers:
- active-satellite TLE ingestion from CelesTrak
- 50 predefined ground stations
- 7-day orbit propagation
- pass prediction for each station-satellite pairing
- filtering out passes shorter than 5 seconds
- pass storage in TimescaleDB
- APIs for health, progress, passes, schedules, and network summary
- automated startup from `docker compose up`

The repository does not try to be a full distributed production system. It keeps the architecture intentionally compact and documents how it can scale further.

## What The System Does
At startup the service:
1. waits for Postgres and Redis
2. runs database migrations
3. seeds ground stations if needed
4. fetches TLEs if data is missing or stale
5. starts background pass computation
6. starts a 24-hour refresh scheduler

The API remains available while the initial pass backfill is running.

## Core Concepts
### TLE
A TLE, or Two-Line Element set, is the compact orbital data format used by the system as the input for orbit propagation.

### SGP4
SGP4 is the orbital propagation model used to estimate satellite positions and velocities from TLE data.

### Ground Pass
A pass is the interval during which a satellite is above the local horizon of a ground station.

Stored pass fields include:
- `aos`
- `los`
- `tca`
- `max_elevation_deg`
- `duration_seconds`
- optional AOS and LOS azimuths

### Hypertable
The `passes` table is stored as a TimescaleDB hypertable partitioned on `aos`, because the main workload is time-window querying.

### Interval Scheduling
A ground station cannot track overlapping passes simultaneously. The system uses greedy interval scheduling per station to select the maximum number of non-overlapping passes.

### Network Coverage Heuristic
The PDF asks for maximizing trackable objects through the network. The project reports network-wide scheduled coverage using a greedy heuristic over already-feasible station schedules. It prioritizes earliest-finishing passes while avoiding repeat coverage of the same satellite where possible.

## Architecture
### Services
The project runs three containers:
- `api`: FastAPI app, startup orchestration, scheduler, and compute trigger
- `db`: TimescaleDB/PostgreSQL
- `redis`: lock coordination and status storage

### Data Flow
```text
CelesTrak
   |
   v
TLE Fetcher
   |
   v
TimescaleDB <-> Pass Computation Pipeline <-> Scheduler
   |                                        |
   +---------------- FastAPI ---------------+
                     |
                     v
                   Redis
```

### Why This Architecture
- FastAPI gives a clean request/response layer and schema validation
- TimescaleDB matches the time-series nature of pass data
- Redis is enough for lightweight state and locking
- APScheduler handles periodic refresh without extra infrastructure
- `ProcessPoolExecutor` provides multi-process computation while keeping deployment simple

## Repository Structure
```text
app/
  main.py
  config.py
  database.py
  models.py
  schemas.py
  seeds/ground_stations.json
  services/
    orchestration.py
    tle.py
    pass_prediction.py
    scheduling.py
    status.py
    redis_state.py
alembic/
samples/
tests/
README.md
ARCHITECTURE.md
docker-compose.yml
Dockerfile
requirements.txt
```

## Data Model
### `ground_stations`
Stores the station catalog with a unique `station_code` for idempotent seeding.

### `satellites`
Stores one logical row per NORAD object.

### `tle_snapshots`
Stores versioned TLE history with a current snapshot marker.

### `passes`
Stores final pass windows and pass summary fields.

### `job_runs`
Stores lifecycle and progress of background jobs, especially initial pass computation.

## Storage And Indexing Strategy
The most important data-structure choices are in the `passes` table:
- hypertable on `aos`
- composite index on `(station_id, aos)`
- composite index on `(satellite_id, aos)`
- overlap-supporting index on `(station_id, aos, los)`

These choices align with the actual query patterns:
- station plus time-range queries
- satellite plus time-range queries
- schedule computation over overlapping intervals

## Computation Logic
### TLE Ingestion
The TLE ingestion flow is:
1. fetch raw TLE text from CelesTrak
2. parse `(name, norad_id, line1, line2, epoch, fetched_at)`
3. upsert `satellites`
4. upsert `tle_snapshots`
5. mark the newest snapshot as current

### Propagation Window
Default configuration:
- prediction horizon: 7 days
- coarse step size: 60 seconds
- minimum pass duration: 5 seconds
- horizon threshold: elevation greater than 0 degrees

### Pass Prediction Pipeline
For each satellite:
1. build a time grid for the future window
2. propagate ECI positions using `sgp4_array()`
3. convert positions from ECI to ECEF
4. convert each station from geodetic coordinates to ECEF
5. compute topocentric elevation and azimuth
6. detect candidate visible intervals
7. refine AOS and LOS at 1-second resolution
8. compute TCA and max elevation
9. store accepted passes in batches

### Why 60-Second Sampling
A 1-second grid across all satellites and all stations for seven days would be expensive for a prototype. The system uses a coarse 60-second scan to find candidate visibility windows, then refines only the edges at 1-second resolution.

## Scheduling Logic
### Station-Level Scheduling
For a single station, the project uses greedy interval scheduling:
- sort by earliest finish time
- select a pass if it starts after the previously selected pass ends

This is optimal for maximizing the count of non-overlapping passes on one station.

### Network-Level Coverage
For network reporting, the service computes a resource-feasible greedy heuristic across station schedules. This is not presented as a mathematically exact global optimizer, but it directly addresses the PDF requirement to reason about network-level tracking coverage.

## Cold Start Behavior
Cold start is an explicit design concern in this project.

On a fresh launch:
- the API starts immediately
- initialization runs in the background
- `/status` returns `202` while computation is running
- `/passes` becomes queryable as batches are inserted

If the pass job fails:
- `/status` returns `503`
- failure information is preserved in `job_runs`

## Setup
### Prerequisites
You need:
- Docker with Compose support
- internet access to `https://celestrak.org`

### Start The Project
From the repository root:
```bash
docker compose up --build
```

Services:
- API: `http://localhost:8000`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

### Clean Restart
To recreate everything from scratch:
```bash
docker compose down -v
docker compose up --build
```

## Startup Sequence
The startup path is:
1. dependency readiness check
2. Alembic migration
3. job-state restoration
4. station seeding
5. TLE refresh if missing or stale
6. initial pass compute trigger if required
7. APScheduler startup for 24-hour refresh

## API Reference
### `GET /health`
Purpose:
- report DB status, Redis status, last TLE fetch time, and pass computation state

### `GET /status`
Purpose:
- report initialization and backfill progress

Returns:
- `state`
- `total_satellites`
- `total_passes_computed`
- `computation_progress_percent`
- `started_at`
- `finished_at`

### `GET /stations`
Purpose:
- list the 50 seeded stations

### `GET /passes`
Purpose:
- return pass windows for a station in a requested time range

Query params:
- `station_id` or `station_code`
- `start`
- `end`
- optional `satellite_id`
- optional `limit`
- optional `offset`

### `GET /schedule/{station_code}`
Purpose:
- return the station-level non-overlapping schedule for a time window

### `GET /network/summary`
Purpose:
- return network-wide pass counts, scheduled counts, and coverage metrics

## Example Requests
```text
GET /health
GET /status
GET /stations
GET /passes?station_code=STN001&start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
GET /schedule/STN001?start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
GET /network/summary?start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
```

## Testing And Validation
The repository includes:
- unit tests for TLE parsing, propagation helpers, scheduling, and status math
- integration-oriented API tests
- sample JSON outputs in `samples/`

Recommended validation flow after startup:
1. call `/health`
2. call `/status`
3. confirm `/stations` returns 50 records
4. query `/passes` for `STN001`
5. query `/schedule/STN001`
6. query `/network/summary`

## Trade-Offs
### TimescaleDB Instead Of SQLite
This problem is naturally time-series oriented, and the evaluation emphasizes efficient data structures and query performance. TimescaleDB is a stronger fit.

### Pass Windows Instead Of Raw Propagated Positions
The system stores final pass outputs rather than every propagated state vector to keep storage size manageable.

### `ProcessPoolExecutor` Instead Of Celery
This keeps the prototype easy to run while still showing multi-process computation. A queue-based worker system is the next production step.

### Greedy Scheduling
Greedy scheduling is exact for station-level interval selection and easy to explain. The network-level logic is a documented heuristic rather than an overstated exact optimizer.

## Limitations
This is still a prototype. The intentional boundaries are:
- no distributed worker queue
- no formal latency benchmark proving exact sub-second behavior at very large scale
- no globally optimal network-wide mathematical scheduler
- no authentication or multi-tenant concerns

## Supporting Documents
- `ARCHITECTURE.md` contains deeper design notes and scalability discussion
- `samples/` contains representative API-style outputs

## Summary
This project is a self-contained backend prototype for satellite pass prediction and network coverage analysis. It combines live orbital data ingestion, SGP4 propagation, visibility geometry, time-series storage, scheduling logic, and query APIs into a single service that can be started with Docker and evaluated end to end.

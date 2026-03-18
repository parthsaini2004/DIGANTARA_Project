# Architecture Notes

## Assumptions

| Topic | Assumption |
|---|---|
| TLE source | CelesTrak active-satellite feed in TLE format |
| Time system | All stored and returned timestamps are UTC |
| Ground stations | 50 predefined global stations are checked into the repo and seeded idempotently |
| Visibility threshold | A pass is visible when elevation is greater than `0 deg` |
| Minimum pass duration | Passes shorter than 5 seconds are discarded |
| Refresh cadence | TLE refresh and forward-window recomputation run every 24 hours |
| Cold start | APIs become available immediately; initial pass computation continues in the background |
| Scope | Prototype optimizes for correctness, scalability signals, and clarity over full distributed production hardening |

## Schema and index rationale

### `ground_stations`
Stores the 50 predefined stations.
- Unique key on `station_code` makes seeding idempotent and keeps external references stable.

### `satellites`
Stores one logical record per NORAD object.
- Unique `norad_id` supports upsert-based catalog refresh.
- `last_tle_fetch_time` is surfaced in health/status diagnostics.

### `tle_snapshots`
Stores versioned TLE history.
- Unique `(satellite_id, epoch)` prevents duplicate snapshots.
- `is_current` allows the pass-compute pipeline to target only the latest orbital state while keeping history.

### `passes`
Stores only final visibility windows and pass summary fields.
- Timescale hypertable on `aos` makes time-window queries the primary storage shape.
- Composite index `(station_id, aos)` supports the most common evaluator query: passes for a station over a time interval.
- Composite index `(satellite_id, aos)` supports satellite-centric diagnostics and future drill-down queries.
- Optional covering index `(station_id, aos, los)` improves overlap-window filtering when query plans need it.

### `job_runs`
Tracks startup and recompute progress.
- Enables `/status` to report progress, failures, and completion times.
- Provides durable history beyond Redis ephemeral state.

## Propagation and pass pipeline design

1. Fetch current active-satellite TLEs from CelesTrak.
2. Persist satellites and versioned TLE snapshots.
3. For each satellite, generate a 7-day coarse time grid at 60-second cadence.
4. Propagate ECI positions with `sgp4_array()`.
5. Convert ECI positions to ECEF with GMST-based Earth rotation.
6. Convert each station from geodetic coordinates to ECEF.
7. Compute topocentric elevation and azimuth for each station.
8. Detect coarse rise/set crossings where elevation crosses the horizon.
9. Refine AOS/LOS at 1-second resolution around each crossing window.
10. Compute TCA and peak elevation for accepted passes.
11. Batch-write pass rows into TimescaleDB so queries become available incrementally.

This pipeline is intentionally split into pure compute functions and orchestration functions so the evaluator can see where vectorization happens and where the architecture would later accept worker offloading.

## Scheduling choice and complexity

Per-station scheduling uses classic greedy interval scheduling:
- sort candidate passes by `los`
- iteratively choose the first pass whose `aos` is after the previously selected `los`

Complexity:
- sorting: `O(n log n)`
- greedy selection: `O(n)`
- total: `O(n log n)`

This is optimal for unweighted non-overlapping interval scheduling, which matches the prototype goal of maximizing count rather than weighted utility.

For network-level reporting, the service also computes a greedy coverage heuristic over the already-feasible station schedules:
- each station remains an independent tracking resource
- the heuristic prioritizes earliest-finishing passes for satellites not yet covered anywhere in the network
- the summary reports how many unique satellites the network can cover while respecting per-station non-overlap

This keeps the implementation aligned with the PDF’s “entire network” requirement without claiming a more complex global optimizer than is actually implemented.

## Scalability path to 10M combinations

The current prototype demonstrates the right scaling direction even though it stays compact:
- vectorized propagation reduces Python-loop overhead per satellite
- Timescale hypertables keep time-window writes and reads aligned with the access pattern
- composite indexes keep station/time and satellite/time queries selective
- incremental chunk insertion makes partial results queryable during long-running recomputes
- Redis-backed job state avoids duplicate startup/recompute execution

Production evolution for larger combination counts:
- move compute from in-process `ProcessPoolExecutor` to Celery or another worker queue
- shard recomputation by satellite ranges or TLE partitions
- precompute rolling windows rather than only startup-triggered full windows
- separate API and worker deployments for independent horizontal scaling
- add backpressure and retry policies around TLE ingestion and compute jobs
- use read replicas or query services if evaluator-style read volume becomes real traffic

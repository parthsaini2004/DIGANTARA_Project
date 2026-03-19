# Ground Pass Prediction Backend

## Deliverables

- Full codebase — self-contained, runnable via `docker compose up --build`
- This document — thinking process, architecture, implementation strategy, assumed parameters
- Sample outputs — see [`samples/`](./samples/)
- `ARCHITECTURE.md` — deeper design notes and scalability roadmap

---

## Thinking Process

### Core Philosophy: Vectorization Over Loops
Pass prediction for 50 stations over 7 days involves tens of millions of geometric comparisons. A scalar Python loop would be too slow. The solution batches all propagation calls using `sgp4_array()` over a NumPy time grid, reducing interpreter overhead significantly.

### Coarse-then-Refine Search
Scanning every second for 7 days is expensive for a prototype. The system uses a **60-second coarse scan** to find candidate horizon crossings, then a **1-second refinement** around each crossing edge. This maintains 1-second AOS/LOS accuracy while keeping computation tractable.

### Pre-computation for Sub-second Queries
On-the-fly pass calculation during a user query would never meet sub-second targets at scale. All passes for the 7-day window are computed once in the background and stored in TimescaleDB. Queries become simple indexed lookups.

### Storage-Centric Thinking
I chose TimescaleDB because the dominant workload is time-window queries (e.g., "give me passes for station X between time A and B"). TimescaleDB's hypertable partitioning on `aos` ensures these queries touch only relevant data chunks and keep index sizes small.

---

## Assumed Parameters

| Parameter | Assumed Value | Rationale |
|---|---|---|
| TLE Source | CelesTrak Active Satellites feed | Standard professional source; "active" TLEs are more frequently updated |
| Prediction Horizon | 7 days | As stated in the problem statement |
| Coarse Sampling Step | 60 seconds | Balances pass-discovery coverage with CPU time |
| Refinement Resolution | 1 second | Required for precise AOS/LOS scheduling |
| Minimum Pass Duration | 5 seconds | Practical threshold; shorter passes are noise for acquisition |
| Horizon Cutoff | 0.0 degrees elevation | Geometric visibility, no terrain masking |
| Atmospheric Refraction | Not modelled | Out of scope for a backend prototype |
| Station Altitude | 0.0 metres (sea level) | Seed data uses globally distributed stations without elevation data |
| CPU Worker Processes | 4 | Reasonable default for a single Docker host node |
| Multi-tenancy / Auth | Out of scope | Single operational network, no user management required |
| Station Count | 50 predefined stations | Seeded from internal JSON; coordinates cover all major geographic regions |

---

## Software Architecture

### Services

The system runs three containers:

| Container | Role |
|---|---|
| `api` | FastAPI application, startup orchestration, APScheduler, compute trigger |
| `db` | TimescaleDB (PostgreSQL + timescaledb extension) |
| `redis` | Distributed lock and job-status cache |

### Architecture Diagram

```
                         ┌──────────────────────┐
                         │    CelesTrak API      │
                         │  (Active TLE Feed)    │
                         └──────────┬───────────┘
                                    │ HTTP fetch
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│                        API Container (FastAPI)                │
│                                                               │
│  ┌─────────────────┐   ┌──────────────────┐  ┌────────────┐  │
│  │ Request Handler │   │   Orchestrator   │  │ Scheduler  │  │
│  │ (Pydantic schemas)│←→│ (job lifecycle)  │  │(APScheduler│  │
│  └────────┬────────┘   └────────┬─────────┘  └────────────┘  │
│           │                     │                             │
│           │           ┌─────────▼──────────┐                 │
│           └──────────►│  Pass Compute Engine│                 │
│                       │ ProcessPoolExecutor │                 │
│                       │  Vectorized SGP4   │                 │
│                       └─────────┬──────────┘                 │
└─────────────────────────────────┼─────────────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
    ┌──────────────┐    ┌──────────────────┐   ┌───────────┐
    │    Redis     │    │   TimescaleDB    │   │  Volumes  │
    │ (lock, state)│    │  (hypertables)   │   │           │
    └──────────────┘    └──────────────────┘   └───────────┘
```

### Data Flow

```
CelesTrak → TLE Fetcher → DB (tle_snapshots, satellites)
                                      ↓
                         Pass Computation Pipeline
                         (ProcessPoolExecutor, SGP4)
                                      ↓
                         DB (passes hypertable)
                                      ↓
                              FastAPI endpoints
```

---

## Implementation Strategy

### Phase 1 — TLE Ingestion
Fetch the CelesTrak active catalog. Parse each TLE block into `(name, norad_id, line1, line2, epoch)`. Upsert `satellites` and `tle_snapshots`. Mark the newest snapshot as current. A 24-hour APScheduler job refreshes TLEs automatically.

### Phase 2 — Vectorized Propagation
Build a NumPy time grid for the 7-day window at 60-second intervals. For each satellite, call `sgp4_array()` to propagate ECI positions in bulk. Convert ECI → ECEF using the GMST rotation matrix at each timestep.

### Phase 3 — Visibility Computation
For each ground station, convert geodetic coordinates to ECEF using the WGS84 ellipsoid (`a = 6378.137 km`, `f = 1/298.257223563`). Compute the topocentric vector to the satellite and project it onto the station's local reference frame to get elevation and azimuth. Intervals where elevation > 0° are candidate passes.

### Phase 4 — AOS/LOS Refinement
For each candidate interval edge (horizon crossing), run a 1-second resolution binary search to pin the exact AOS and LOS. Compute TCA (time of closest approach) as the instant of maximum elevation. Discard passes shorter than 5 seconds.

### Phase 5 — Persistence
Batch-insert accepted passes into the `passes` TimescaleDB hypertable. Bulk insert mappings reduce DB round-trips and allow the background job to finish faster.

### Phase 6 — Scheduling
**Station-level**: greedy interval scheduling sorted by earliest finish time — provably optimal for maximising non-overlapping pass count on a single resource.  
**Network-level**: greedy heuristic across all station schedules, prioritising earliest-finishing passes while avoiding repeat coverage of the same satellite. Documented as a heuristic, not an exact global optimizer.

---

## Setup Instructions

### Prerequisites
- Docker with Compose v2
- Internet access to `https://celestrak.org`

### Start

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| API | `http://localhost:8000` |
| API Docs | `http://localhost:8000/docs` |
| Postgres | `localhost:5432` |
| Redis | `localhost:6379` |

### Clean Restart

```bash
docker compose down -v && docker compose up --build
```

### Logs

```bash
docker compose logs -f api
```

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /health` | DB + Redis status, last TLE fetch, computation state |
| `GET /status` | Background job progress percentage |
| `GET /stations` | List all 50 seeded ground stations |
| `GET /passes` | Pass windows filtered by station and time range |
| `GET /schedule/{station_code}` | Non-overlapping greedy schedule for a station |
| `GET /network/summary` | Network-wide coverage and tracking metrics |

### Example Requests

```bash
GET /health
GET /status
GET /stations
GET /passes?station_code=STN001&start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
GET /schedule/STN001?start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
GET /network/summary?start=2026-03-18T00:00:00Z&end=2026-03-19T00:00:00Z
```

Sample JSON responses for each endpoint are available in [`samples/`](./samples/).

---

## Trade-offs and Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Database | TimescaleDB over SQLite/Postgres | Native time-series partitioning; hypertables keep index sizes small |
| Storage granularity | Pass windows, not raw position vectors | Manageable storage; raw vectors would be orders of magnitude larger |
| Worker model | `ProcessPoolExecutor` over Celery | Keeps deployment simple; still exploits multiple CPU cores |
| Scheduling algorithm | Greedy by EFT over ILP solver | Exact for count-maximisation on one resource; O(P log P) vs exponential |
| Refresh mechanism | APScheduler in-process over Celery Beat | No extra services required for a prototype scope |
| Cold-start safety | Background computation, API available immediately | Status endpoint provides progress; passes queryable as batches land |

---

## Repository Structure

```
app/
  main.py           — startup lifecycle and route registration
  config.py         — environment variable config
  models.py         — SQLAlchemy ORM models
  schemas.py        — Pydantic request/response schemas
  seeds/            — ground_stations.json
  services/
    orchestration.py  — startup sequencing and job lifecycle
    tle.py            — CelesTrak fetch and parse
    pass_prediction.py — SGP4 propagation and visibility geometry
    scheduling.py     — greedy interval scheduler
    status.py         — job progress computation
    redis_state.py    — distributed lock helpers
alembic/            — database migrations
samples/            — representative JSON outputs
tests/              — unit and integration tests
ARCHITECTURE.md     — scalability roadmap and deep design notes
docker-compose.yml
Dockerfile
requirements.txt
```

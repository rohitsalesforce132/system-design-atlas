# Uber — System Design Atlas

> **One-line summary:** Uber is a real-time two-sided marketplace that connects riders with nearby
> drivers, dispatching a car in ~1 second using geospatial indexing, WebSocket push, and a
> microservices backend that must be correct under concurrent state transitions (only one rider can
> claim a driver).

---

## 1. Overview & Scale Numbers

Uber's core problem is harder than most apps because it is **a marketplace, a real-time location
system, and a payment system at the same time**. You can tolerate a 200ms delay in a Google search
result; you cannot tolerate a 5-second delay before your Uber shows up, because the car is
physically moving.

### The numbers

| Metric                                      | Approximate value          | Why it matters                                         |
| ------------------------------------------- | -------------------------- | ------------------------------------------------------ |
| Daily bookings                              | ~25M+ (2024)               | Peak load concentrates in cities + weekend nights      |
| Cities active                               | 10,000+ across 70+ countries | Each city is a mostly-independent operational unit     |
| Drivers online                              | ~7M+ active                | Each one emits GPS pings every 1–5s                    |
| Trips per second at peak                    | thousands globally         | Dispatch latency target: <2s end-to-end                |
| GPS location updates per second             | tens of thousands          | Drives the entire geo-index architecture               |
| Estimated daily distance driven             | ~1.5B+ miles               | Pricing + routing must scale                           |
| Estimated trips/year                        | ~9B                        | Each trip = ~5 state transitions (request→arrive→...)   |
| Latency budget for a dispatch decision      | ~1 second                  | Beyond that, car has moved; match becomes stale         |

### The product goal in one paragraph

A rider opens the app, sees nearby cars on a map within 1 second, requests a ride, and within ~3
seconds gets matched to a real driver who then drives to them. Surge pricing dynamically balances
supply and demand. Payment is automatic, receipt arrives by email. Every step must be resilient to
network issues on phones with intermittent GPS and battery constraints.

---

## 2. High-Level Architecture

Uber is fundamentally a **discrete event system**. The interesting state transitions are:

```
   rider requests  →  driver matched  →  driver arrives  →  trip starts
        →  trip ends  →  payment + receipt
```

Everything else (maps, ETAs, surge pricing, ratings) is built to support these transitions. The
high-level diagram below splits the system into the rider app, driver app, dispatch core, and
supporting services.

```
   ┌───────────────────┐                          ┌────────────────────┐
   │   RIDER APP       │                          │   DRIVER APP       │
   │  - shows map      │                          │  - receives trips  │
   │  - surge pricing  │                          │  - broadcasts GPS  │
   │  - live ETA       │                          │  - accepts/declines│
   └─────────┬─────────┘                          └──────────┬─────────┘
             │  HTTPS (REST + WebSocket)                      │
             │                                               │
             ▼                                               ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                        API GATEWAY / LOAD BALANCER                  │
   │                  (TLS, auth, rate limit, routing)                  │
   └─────────────────────────────┬───────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
   ┌──────────┐            ┌──────────────┐           ┌──────────────┐
   │ Passenger │            │   Driver     │           │  Surge /     │
   │  Service  │            │   Service    │           │  Pricing Svc │
   │  (CRUD)   │            │  (CRUD)      │           │              │
   └──────────┘            └──────┬───────┘           └──────────────┘
                                    │
                                    │ GPS pings, status
                                    ▼
                          ┌──────────────────────┐
                          │   Location /         │   ◀────── Quadtree /
                          │   Geospatial Index   │           Geohash
                          │   (in-memory, sharded│
                          │    by city/geo cell) │
                          └──────────┬───────────┘
                                     │ nearby drivers query
                                     ▼
                          ┌──────────────────────┐
                          │   DISPATCH           │
                          │   (core matching)    │
                          │   - find candidates  │
                          │   - filter / rank    │
                          │   - assign atomically│
                          └──────────┬───────────┘
                                     │  trip lifecycle
                                     ▼
   ┌──────────┐    ┌──────────┐   ┌──────────────┐   ┌──────────────┐
   │  ETA     │    │  Pricing │   │   Trip       │   │  Payment     │
   │  Service │    │  Service │   │   State      │   │  Service     │
   │ (routing)│    │ (compute)│   │   Machine    │   │ (charges,    │
   │          │    │          │   │              │   │  receipts)   │
   └──────────┘    └──────────┘   └──────────────┘   └──────────────┘
```

### The key abstraction: the Trip State Machine

Every trip is a state machine with strict transitions. Only one service — the **Dispatch core** —
is allowed to mutate trip state, and it does so atomically using distributed locking (originally
dispatch was a single-service state machine; today it is a set of services coordinating through
Kafka and a strongly consistent store).

```
   [REQUESTED] ──driver accepts──▶ [ACCEPTED] ──driver arrives──▶ [ARRIVING]
                                                                    │
                                                                    │ trip start
                                                                    ▼
   [COMPLETED] ◀──trip ends──── [ONGOING] ◀──rider boards─────────┘
```

---

## 3. Detailed Component Breakdown

### 3.1 Passenger & Driver services

These are classic CRUD services over a relational DB (PostgreSQL/MySQL, originally schema-per-
domain). Passenger service owns accounts, payment methods, ride history. Driver service owns driver
profiles, vehicle info, background-check status, and current status (online/offline).

### 3.2 Location service (the heart of the system)

This is what makes Uber Uber. Drivers' apps emit GPS coordinates every 1–5 seconds. The Location
service must:

1. **Ingest** millions of GPS pings/sec at low latency.
2. **Index** them spatially so that a "find drivers within 2km of (lat,lng)" query is fast.
3. **Expire** stale entries (a driver who hasn't pinged in 30s probably went offline).

Uber uses a combination of **geohash** (a string that encodes lat/lng into a sortable prefix) and
an in-memory **quadtree** that subdivides a city into cells. The quadtree dynamically subdivides
when a cell gets too dense.

```
   City grid, subdivided into cells:

   +───────+───────+───────+───────+
   │       │       │   .   │       │      ← Each cell holds a set of
   │   A   │   B   │   C   │   D   │        driver IDs currently inside it.
   │       │       │  rider│       │
   +───────+───────+───────+───────+      rider at (lat,lng) → find the
   │       │ d1 d2 │ d3    │       │      cell → query neighbors →
   │   E   │       │       │   F   │      candidate driver set {d1,d2,d3}
   │       │       │       │       │
   +───────+───────+───────+───────+
```

A naive "scan all drivers" query is O(N). With cell-based indexing it's O(drivers-in-cell +
neighbors), which at city scale is a few dozen candidates.

### 3.3 Dispatch service

Dispatch takes a ride request and produces a driver assignment. Two phases:

1. **Candidate generation** — query Location service for nearby drivers (within ~3–5 km).
2. **Atomic assignment** — for each candidate, send a trip offer. First driver to accept wins.
   This is the classic Uber "ping several drivers, first accept wins" flow, sometimes called
   *broadcast dispatch*. Originally Uber used a "first assign, then confirm" model but switched to
   broadcast for better conversion.

```
   rider request
        │
        ▼
   ┌──────────────┐
   │   Location   │  ─── candidate list: [d1, d2, d3]
   │   lookup     │
   └──────┬───────┘
          ▼
   ┌──────────────────────────────────────────────┐
   │ DISPATCH broadcasts offer to all candidates  │
   │  (via WebSocket push to each driver app)     │
   └────┬──────────┬───────────┬───────────────────┘
        ▼          ▼           ▼
       d1         d2          d3
        │          │           │
       (ignores)  (accepts!)   │
        │          │           │
        │◀──no─────┤           │
        │          ├──accept──▶│ ◀── atomic write: trip.driver_id = d2
        │          │           │
                  ▼
           other candidates get "trip taken" notification
```

The atomicity is critical: **only one rider can win a driver.** This is enforced via a
strongly-consistent write (e.g. a conditional update in Cassandra, or a Redis/etcd lock, or a
transactional update in the trip store).

### 3.4 ETA service

ETA = Estimated Time of Arrival. When you see "4 min" on the map, that came from the ETA service.
It runs a **routing engine** (historically OSRM or a custom graph-based router) over a road
network graph, plus a machine-learning model that adjusts for traffic, time of day, and weather.

Two kinds of ETA:
- **Pickup ETA:** driver → rider. Uses real-time driver location + routing.
- **Trip ETA:** rider → destination. Uses the routing engine + traffic model.

### 3.5 Pricing & Surge

Pricing multiplies a base fare by time + distance and applies a **surge multiplier** when demand
exceeds supply in a cell. Surge is computed per cell (not globally) using the ratio of ride
requests to available drivers over a recent time window.

```
   surge_multiplier = f(requests_per_min, available_drivers)

   if requests >> drivers  →  multiplier goes up  →  more drivers come online
                                                   →  some riders give up
                                                   →  market rebalances
```

Surge values are published to a cache and read by the rider app.

### 3.6 Trip State Machine service

Owns the authoritative state of every trip. Built on a strongly consistent store (originally
Riak; today a mix of consensus-based systems). Every state transition is an event appended to an
event log (Kafka) so downstream services can react: Payment listens for `COMPLETED`, ETA listens
for `ONGOING`, Analytics consumes everything.

### 3.7 Payment service

Listens for `TRIP_COMPLETED` events on Kafka, computes the final fare (base + time + distance +
surge - promotions), charges the stored payment method via Stripe/Adyen/Stripe-equivalent
integrations, and emits a receipt. Payment is the classic "strong consistency required" domain —
money must be exactly-once.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │   Rider      │     │   Driver     │     │   Vehicle        │
   │ - id         │     │ - id         │     │ - id             │
   │ - name       │     │ - name       │     │ - driver_id      │
   │ - phone      │     │ - rating     │     │ - plate, model   │
   │ - payment_id │     │ - status     │     │ - capacity       │
   └──────────────┘     └──────────────┘     └──────────────────┘
                                                                │
   ┌────────────────────────────────────────────────────────────┘
   │
   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                       TRIP                                   │
   │ - id, rider_id, driver_id (null until assigned)             │
   │ - pickup_geo, dest_geo                                      │
   │ - status (REQUESTED/ACCEPTED/ONGOING/COMPLETED/CANCELLED)   │
   │ - surge_multiplier, fare, currency                          │
   │ - created_at, accepted_at, started_at, ended_at             │
   └────────────────────────────┬─────────────────────────────────┘
                                │ 1
                                │
                                ▼ *
                       ┌──────────────────┐
                       │  Trip Event      │   (event-sourced log)
                       │ - trip_id        │
                       │ - event_type     │
                       │ - lat/lng/ts     │
                       │ - payload        │
                       └──────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                          | Why                                  |
| ------------------------------- | ------------------------------ | ------------------------------------ |
| Rider/Driver accounts           | MySQL/PostgreSQL (sharded)     | Strong consistency, transactional    |
| Trip records (current state)    | Strongly consistent KV / MySQL | Only one writer per trip; correctness |
| Trip events (history)           | Kafka → S3/warehouse           | Append-only, analytics-friendly      |
| Live driver locations           | In-memory geo index (Redis + custom) | Sub-second reads/writes         |
| ETA / routing graph             | In-memory graph (OSRM)         | Sub-100ms path queries               |
| Receipts / payments             | MySQL + event log              | Auditability, exactly-once           |
| Push tokens (mobile)            | Redis / DynamoDB               | Fast lookup by user_id               |

### 4.3 Why the live location is in-memory

Driver GPS is updated every few seconds and read every time a rider opens the app. A disk-backed
DB cannot serve that read/write ratio at the required latency. The Location service keeps an
in-memory quadtree/geohash index; if a node crashes, drivers re-emit their location within seconds
and the index rebuilds.

---

## 5. Request Flow — Booking a Ride

```
RIDER APP     API GW     PASSENGER    LOCATION    DISPATCH     DRIVER APP    TRIP STATE
   │             │           SVC          SVC          │             │           │
   │─open app───▶│           │            │            │             │           │
   │             │─auth──────▶│            │            │             │           │
   │             │           │─nearby?────▶│           │             │           │
   │             │           │◀─driver set─┤           │             │           │
   │◀──cars on map + ETA────┤            │            │             │           │
   │             │           │            │            │             │           │
   │─request ride▶│          │            │            │             │           │
   │             │─create trip ─────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │             │           │            │   dispatch picks candidates ───────▶│
   │             │           │            │            │             │           │
   │             │           │            │            │──offer (WS)────────────▶│
   │             │           │            │            │             │           │
   │             │           │            │            │◀─────accepts! ──────────┤
   │             │           │            │            │             │           │
   │             │           │            │            │──atomic assign─────────▶│
   │             │           │            │            │             │           │
   │◀───driver info + ETA────┤◀─────────────────────────────────────────────────┤
   │             │           │            │            │             │           │
   │  driver drives to pickup; GPS pings flow every 1-5s                          │
   │             │           │            │            │             │           │
   │◀──live driver position updates via WebSocket─────────────────────────────────│
   │             │           │            │            │             │           │
   │─"arrived" detected (geo-fence)─────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │─rider boards; trip ONGOING ────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │   driver drives; GPS pings continue; ETA recomputed                          │
   │             │           │            │            │             │           │
   │─arrive at destination ──────────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │             │                                                        │
   │             │      Kafka: TRIP_COMPLETED event ────────────────────▶│
   │             │                                              PAYMENT SVC │
   │             │                                              ─charges card│
   │◀──receipt + rating prompt───────────────────────────────────────────┤
```

**Step-by-step:**

1. **Rider opens app.** App calls `/nearbyDrivers?lat&lng`. API GW routes to Passenger service.
2. **Passenger service asks Location service** for drivers within ~2km. Returns ~5–20 driver IDs +
   positions. App plots them on the map.
3. **Rider enters destination, requests ride.** A `Trip` is created in `REQUESTED` state.
4. **Dispatch picks candidates** from Location service (a slightly wider radius now), filtered by
   vehicle type, driver rating, and recent cancellation history.
5. **Offer is broadcast** to candidates over WebSocket. Each driver app shows the offer screen
   (pickup, dest, ETA, fare estimate).
6. **First driver to accept wins.** Driver app sends `ACCEPT` with `trip_id`. Trip State service
   does a conditional update — if `status == REQUESTED`, set `driver_id = X` and `status =
   ACCEPTED`. Other drivers get a "trip taken" push.
7. **Rider is notified** of the matched driver, vehicle, plate, ETA.
8. **Driver drives to pickup.** GPS pings update the Location service; rider app receives position
   updates over WebSocket.
9. **Arrival detected** (geo-fence: driver within ~50m of pickup). Trip transitions to
   `ARRIVING` → rider gets a push.
10. **Trip starts.** Driver presses "Start trip"; trip → `ONGOING`. ETA service starts recomputing
    trip ETA as driver moves.
11. **Trip ends.** Driver presses "End trip" near destination; trip → `COMPLETED`. A
    `TRIP_COMPLETED` event lands on Kafka.
12. **Payment service** consumes the event, computes fare, charges the card, sends receipt.
13. **Rating prompt** shown to both rider and driver.

---

## 6. Scaling Strategy

### 6.1 Shard by city

Uber's traffic is geographically clustered. A rider in São Paulo never shares state with a driver
in Delhi. Uber shards the Location service and Dispatch by **city / region**, so each shard only
handles its own geo cells.

### 6.2 In-memory geo index

The Location service is the hottest path. It is in-memory (Redis + custom quadtree), sharded by
geo cell, and stateless above the index — a dead node is rebuilt from the next round of GPS pings
within seconds.

### 6.3 Event-driven downstream services

Payment, Analytics, Receipts, Fraud all consume from Kafka. They never participate in the
critical dispatch path, so a Payment outage doesn't break ride booking.

### 6.4 Idempotent writes

Every API call carries an idempotency key. If a rider's flaky network causes a double-tap
"request ride", the backend deduplicates. Payment uses exactly-once semantics via idempotency
keys on the payment provider.

### 6.5 WebSocket fan-out for driver push

Each driver app holds a WebSocket to a **connection server** (think of it as a chat server). When
Dispatch wants to ping 5 drivers, it sends 5 messages to the connection servers responsible for
those drivers. The connection server layer is scaled horizontally; millions of open sockets are
normal (think: WhatsApp-scale fan-out, but per-city).

### 6.6 Multi-region + disaster recovery

Uber runs multiple datacenters per region with the ability to fail over. Consistency for trip
state is achieved via consensus (Raft/Paxos-style) within a region.

---

## 7. Tech Stack

| Layer                       | Technology                                            |
| --------------------------- | ----------------------------------------------------- |
| Mobile apps                 | Swift (iOS), Kotlin (Android), RIBs architecture      |
| Backend languages           | Go, Java, Python, Node.js (legacy)                    |
| API gateway                 | Custom, on top of NGINX/envoy                         |
| Databases                   | PostgreSQL, MySQL, Cassandra, Riak (legacy)           |
| Schema management           | Schemaless (Uber's in-house store on MySQL)           |
| In-memory                   | Redis                                                 |
| Streaming                   | Apache Kafka                                          |
| Service mesh                | Shark / uMesh (custom)                                |
| Geospatial                  | Custom quadtree + Geohash; OSRM / custom routing      |
| ML / ETA / pricing          | Python, Michelangelo (Uber's ML platform)             |
| Maps                        | Custom rendering pipeline; data from multiple vendors |
| Deployment                  | Peloton (custom schedular), internally built CI/CD    |
| Observability               | M3 (metrics), Jaeger (tracing), custom log pipeline   |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐                  ┌────────────────────┐                ┌────────────┐
   │  Rider Web │◀────REST + WS────▶│   Node.js backend  │◀───REST───────▶│ Driver Web │
   │  (browser) │                  │  - Express         │                │ (browser)  │
   └────────────┘                  │  - Socket.io       │                └────────────┘
                                   │  - Postgres        │
                                   │  - Redis (geo)     │
                                   └────────────────────┘
```

### 8.2 The geo index in 30 lines

The trick: use **Redis GEO** commands. Redis has `GEOADD`, `GEORADIUS` (or `GEOSEARCH`), which
internally uses geohash sorted sets.

```python
# driver pings location
redis.geoadd("drivers_online", lng, lat, driver_id)

# rider asks for nearby drivers
nearby = redis.geosearch(
    "drivers_online",
    longitude=rider_lng, latitude=rider_lat,
    radius=2, unit="km",
    withcoord=True, count=20
)
```

That's the core of Uber's location lookup, in two Redis calls.

### 8.3 The dispatch logic

```python
# rider requests ride
candidates = redis.geosearch(...)

# broadcast offer to each candidate driver via Socket.io
for driver_id in candidates:
    socketio.emit("ride_offer", offer_payload, to=driver_id)

# first driver to accept wins (atomic)
@socketio.on("accept_ride")
def on_accept(trip_id, driver_id):
    # conditional update in Postgres: UPDATE trips SET driver_id=..., status='ACCEPTED'
    #                            WHERE id=... AND status='REQUESTED'
    rows = db.execute("""
        UPDATE trips
        SET driver_id = %s, status = 'ACCEPTED'
        WHERE id = %s AND status = 'REQUESTED'
        RETURNING id
    """, (driver_id, trip_id))
    if rows:  # this driver won the race
        socketio.emit("ride_accepted", {...}, to=rider_id)
        # tell other candidates the trip is taken
        socketio.emit("ride_taken", {...}, to=other_drivers)
```

### 8.4 ETA

Use the open-source **OSRM** demo server or the **Open Source Routing Machine** locally. Or call
the free OSRM API:

```python
import requests
r = requests.get(f"http://router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}")
duration = r.json()["routes"][0]["duration"]  # seconds
```

### 8.5 Map

Drop in **Leaflet.js** + OpenStreetMap tiles — free, no API key. Plot drivers as markers; update
positions via Socket.io events.

### 8.6 Surge pricing (simplified)

```python
recent_requests = redis.incr(f"req_count:{cell_id}:{minute}")  # per-minute counter
available_drivers = redis.zcard(f"drivers:{cell_id}")
ratio = recent_requests / max(available_drivers, 1)
surge = min(2.0, max(1.0, ratio / 3))  # cap at 2x
```

### 8.7 What you'll learn

- How geospatial indexing turns an O(N) problem into O(local).
- How atomic conditional updates solve the "only one rider wins" race.
- How WebSockets enable real-time push at scale.
- Why an event log decouples fast paths (dispatch) from slow paths (payment).

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered        | Why Uber chose it                                       |
| ----------------------------------------------- | ----------------------------- | ------------------------------------------------------- |
| **Broadcast dispatch (ping many, first wins)**  | Single assign + confirm       | Higher acceptance rate; drivers self-select             |
| **In-memory geo index (Redis + quadtree)**      | PostGIS on Postgres           | Sub-millisecond reads/writes for millions of pings      |
| **Shard by city/region**                        | Global sharding               | Geographic locality; fault isolation per city           |
| **Event-sourced trip state (Kafka)**            | Only mutable trip rows        | Replay, analytics, audit, decoupled consumers           |
| **Schemaless on MySQL**                         | Pure Postgres                 | Horizontal scaling with MySQL's operational maturity    |
| **Surge per cell, not global**                  | Global pricing                | Markets are local; surge must be local                  |
| **Idempotency keys on every write**             | Trust client dedup            | Mobile networks are flaky; double-charges are unacceptable |
| **WebSocket to driver app**                     | Polling                       | Sub-second push for offers and position updates         |

### The deepest trade-off

**Correctness vs. latency in dispatch.** Uber could assign the "optimal" driver by running a
global optimization, but that takes seconds and the car has moved. Instead they accept a
"good enough" candidate set and let the first-to-accept win, optimizing for **dispatch latency**
over optimality. This is a deliberate engineering choice driven by the physics of moving cars.

---

## 10. Common Interview Questions

**Q1: How would you design Uber?**
Start with the two-sided marketplace. Split into rider/driver/dispatch. Explain the trip state
machine. Highlight that dispatch must be atomic (only one rider wins a driver). Discuss Location
service with geohash/quadtree.

**Q2: How do you find nearby drivers quickly?**
Geohash or quadtree index in memory. Drivers write GPS every few seconds; rider query is a radial
search over the cell and neighbors. Redis GEO commands do this natively.

**Q3: How do you prevent two riders from getting the same driver?**
Conditional update: `UPDATE trips SET driver_id=? WHERE id=? AND status='REQUESTED'`. Only one
write succeeds. Or a distributed lock with a short TTL.

**Q4: How does surge pricing work?**
Per-cell ratio of requests to available drivers over a time window. Multiplier is published to a
cache; rider app reads it. Goal: rebalance supply/demand.

**Q5: How do you scale to millions of drivers?**
Shard by city. In-memory geo index. WebSocket fan-out via connection servers. Event log for
downstream consumers. Stateless services above the geo index.

**Q6: Why an event-sourced trip state?**
Decouples fast path (dispatch) from slow path (payment, analytics, fraud). Enables replay for
debugging. Audit trail for disputes ("the driver never showed up").

**Q7: What happens if the Location service goes down?**
Drivers re-emit GPS within seconds. The index rebuilds from incoming pings. Critical services are
sharded per city, so a São Paulo outage doesn't affect Delhi.

**Q8: How do you handle a driver going offline mid-trip?**
Timeout + heartbeat. If no GPS ping for N seconds, driver is marked offline; if mid-trip, support
is alerted and the trip may be reassigned. Rider gets a push.

**Q9: How is ETA computed?**
Routing engine (OSRM or custom) over a road graph + ML model that corrects for traffic/time-of-day.
Pickup ETA uses real-time driver location; trip ETA uses destination.

**Q10: Why not just use PostGIS?**
PostGIS radial queries on disk are too slow at millions-of-pings/sec. In-memory geo index gives
sub-millisecond reads. PostGIS is fine for low-volume or batch analytics, not for the hot path.

---

## Further reading

- Uber Engineering Blog (eng.uber.com) — primary source for dispatch, location, Schemaless.
- "Engineering the State Machine Behind Uber's Trip Execution" — Uber's own post.
- "How Uber Uses Kafka" —Uber's event backbone.
- Schemaless paper/blog posts — Uber's MySQL-based scalable store.
- Ringpop — Uber's old consistent hashing + sharding library.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

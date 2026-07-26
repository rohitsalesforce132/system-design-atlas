# Ola — System Design Atlas

> **One-line summary:** Ola is India's homegrown ride-hailing platform — a real-time two-sided
> marketplace matching riders with drivers across India's tier-1/2/3 cities, dispatching a cab in
> seconds using geospatial indexing, WebSocket push, and a microservices backend that must stay
> correct under concurrent state transitions while coping with India's unique challenges:
> intermittent mobile networks, multi-modal rides (Auto, Bike, Cab, e-rickshaw), and cash payments.

---

## 1. Overview & Scale Numbers

Ola (originally Olacabs) was founded in **2010** in Mumbai, later headquartered in Bengaluru. It
predates Uber in India and was the dominant domestic ride-hailing app for most of the 2010s. Unlike
Uber, Ola built for **India-first constraints** from day one: cash payments (most Indians didn't have
cards in 2010), multilingual UIs, autos and bikes alongside cars, and operation across **tier-2/3
cities** where addresses are landmarks, not pin codes.

The product expanded into **Ola Money** (a wallet, now largely pivoted to UPI), **Ola Electric**
(EVs — a separate company), **Ola Foods** (cloud kitchens), and **Ola Cabs / Ola Outstation**
(inter-city). But the core — matching riders and drivers — remains the spine.

### The scale

| Metric                                            | Approximate value                 | Why it matters                                              |
| ------------------------------------------------- | --------------------------------- | ----------------------------------------------------------- |
| Daily rides                                       | ~2–2.5M+                          | Peak load concentrates in city rush hours                   |
| Cities active                                     | ~250+ (India + Australia/NZ/UK)   | Each city is mostly independent operationally               |
| Driver-partners                                   | ~2.5M+                            | Each emits GPS pings every 1–5s                             |
| Ride categories                                   | 6+ (Micro, Mini, Prime, Auto, Bike, Outstation, Rental) | Each category has its own matching logic         |
| Peak rides/sec                                    | thousands                         | Dispatch latency target: <3s                                 |
| GPS updates/sec                                   | tens of thousands                 | Drives the geo-index architecture                           |
| Cash vs. digital payments                         | historically cash-heavy; now Ola Money / UPI mix | Cash handling adds reconciliation complexity    |
| Latency budget for dispatch                       | ~2–3 seconds                      | Beyond that, the auto/cab has moved; match becomes stale    |

### The product goal in one paragraph

A rider opens Ola, sees nearby autos/cabs/bikes on a map within 1–2 seconds, selects a category
(Micro / Auto / Bike), enters a destination, sees an upfront fare estimate, and within ~3–5 seconds
gets matched to a real driver. The driver navigates to pickup, the trip starts, the rider is dropped
at the destination, and payment happens via Ola Money, UPI, or cash. Surge pricing balances supply
and demand per cell. Every step must tolerate flaky 4G networks, drivers switching their phones off
to avoid rides, and the chaos of Indian street addresses.

---

## 2. High-Level Architecture

Ola's architecture is fundamentally a **real-time event system** built around the **trip state
machine**. The interesting transitions are:

```
   rider requests  →  driver matched  →  driver arriving  →  trip ongoing
        →  trip ends  →  payment + receipt + rating
```

```
   ┌───────────────────┐                          ┌────────────────────┐
   │   RIDER APP       │                          │   DRIVER APP       │
   │  - shows map      │                          │  - receives rides  │
   │  - surge overlay  │                          │  - broadcasts GPS  │
   │  - category pick  │                          │  - accept / decline│
   │  - upfront fare   │                          │  - offline mode    │
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
   │  Rider   │            │   Driver     │           │  Surge /     │
   │ Service  │            │   Service    │           │  Pricing Svc │
   │  (CRUD)  │            │  (CRUD)      │           │              │
   └──────────┘            └──────┬───────┘           └──────────────┘
                                    │ GPS pings, status
                                    ▼
                          ┌──────────────────────┐
                          │   Location /         │   ◀────── Quadtree /
                          │   Geospatial Index   │           Geohash
                          │   (in-memory, sharded│
                          │    by city / geo cell)│
                          └──────────┬───────────┘
                                     │ nearby drivers query
                                     ▼
                          ┌──────────────────────┐
                          │   DISPATCH           │
                          │   (core matching)    │
                          │   - find candidates  │
                          │   - filter by type   │
                          │   - assign atomically│
                          └──────────┬───────────┘
                                     │  ride lifecycle
                                     ▼
   ┌──────────┐    ┌──────────┐   ┌──────────────┐   ┌──────────────┐
   │  ETA     │    │  Pricing │   │   Ride       │   │  Payment     │
   │  Service │    │  Service │   │   State      │   │  Service     │
   │ (routing)│    │ (compute)│   │   Machine    │   │ (Ola Money / │
   │          │    │          │   │              │   │  UPI / cash) │
   └──────────┘    └──────────┘   └──────────────┘   └──────────────┘
```

### The key abstraction: the Ride State Machine

Every ride is a state machine with strict transitions. Only the **Dispatch core** is allowed to
mutate ride state, and it does so atomically (distributed lock or conditional update).

```
   [REQUESTED] ──driver accepts──▶ [ACCEPTED] ──driver arrives──▶ [ARRIVING]
                                                                    │
                                                                    │ trip start
                                                                    ▼
   [COMPLETED] ◀──trip ends──── [ONGOING] ◀──rider boards─────────┘
        │
        ├──▶ [PAYMENT_PENDING] ──▶ [CLOSED]
        └──▶ [CANCELLED] (rider / driver / timeout)
```

### India-specific complications

- **Multiple ride categories in one app** — a rider can choose Auto, Bike, Micro, Mini, Prime. Each
  category has its own driver pool, pricing, and matching logic. The dispatch must filter by
  category.
- **Cash payments** — many Indian riders pay cash. The driver's app must record the cash collected,
  and Ola reconciles driver earnings vs. company commission nightly.
- **Landmark-based addresses** — tier-2/3 addresses often lack structured pin codes. The geocoder
  must accept free-text + landmark and map to a lat/lng.
- **Flaky networks** — driver apps must queue GPS pings offline and replay them when reconnected.

---

## 3. Detailed Component Breakdown

### 3.1 Rider & Driver services

Classic CRUD services over a sharded relational DB. Rider service owns profiles, payment methods
(Ola Money balance, UPI ID, saved cards), ride history, ratings. Driver service owns driver
profiles, vehicle info (RC book, insurance, permit — required by Indian regulators), background
check status, and current status (online/offline/on-ride).

### 3.2 Location service (the heart)

Drivers' apps emit GPS coordinates every 1–5 seconds. The Location service must:

1. **Ingest** tens of thousands of GPS pings/sec at low latency.
2. **Index** them spatially — "find autos within 2km of (lat,lng)" must be fast.
3. **Expire** stale entries (a driver who hasn't pinged in 30s probably went offline — common in
   India due to network drops or drivers gaming the system).

Ola uses a combination of **geohash** (string encoding of lat/lng into a sortable prefix) and an
in-memory **quadtree** that subdivides a city into cells. The quadtree dynamically subdivides when a
cell gets too dense (e.g., Connaught Place, Delhi at 6 PM).

```
   City grid (e.g., Bengaluru), subdivided into cells:

   +───────+───────+───────+───────+
   │       │       │   .   │       │      ← Each cell holds a set of
   │   A   │   B   │   C   │   D   │        driver IDs currently inside it.
   │       │       │ rider │       │
   +───────+───────+───────+───────+      rider at (lat,lng) → find the
   │       │ d1 d2 │ d3    │       │      cell → query neighbors →
   │   E   │ (auto)│ (bike)│   F   │      candidate driver set {d1,d2}
   │       │       │       │       │      (filtered by category)
   +───────+───────+───────+───────+
```

A naive "scan all drivers" query is O(N). With cell-based indexing it's O(drivers-in-cell +
neighbors), which at city scale is a few dozen candidates.

### 3.3 Dispatch service

Takes a ride request and produces a driver assignment. Two phases:

1. **Candidate generation** — query Location service for nearby drivers of the requested category
   (within ~2–5 km for cabs, ~1–2 km for autos/bikes).
2. **Atomic assignment** — for each candidate, send a ride offer. First driver to accept wins. This
   is the "broadcast dispatch" pattern.

```
   rider request (category=AUTO)
        │
        ▼
   ┌──────────────┐
   │   Location   │  ─── candidate list: [d1, d2, d3]  (all AUTO drivers)
   │   lookup     │
   └──────┬───────┘
          ▼
   ┌──────────────────────────────────────────────┐
   │ DISPATCH broadcasts offer to all candidates │
   │  (via WebSocket push to each driver app)    │
   └────┬──────────┬───────────┬─────────────────┘
        ▼          ▼           ▼
       d1         d2          d3
        │          │           │
       (ignores)  (accepts!)   │
        │          │           │
        │◀──no─────┤           │
        │          ├──accept──▶│ ◀── atomic write: ride.driver_id = d2
        │          │           │
                  ▼
           other candidates get "ride taken" notification
```

The atomicity is critical: **only one rider can win a driver.** Enforced via a strongly consistent
write (conditional update in the ride store, or a Redis/etcd lock).

### 3.4 ETA service

ETA = Estimated Time of Arrival. When you see "4 min" on the map, that came from the ETA service.
Runs a **routing engine** (OSRM or a custom graph-based router) over India's road network, plus a
machine-learning model that adjusts for traffic, time of day, weather, and local quirks (one-ways,
festival processions, waterlogging in monsoon).

Two kinds of ETA:
- **Pickup ETA:** driver → rider. Uses real-time driver location + routing.
- **Trip ETA:** rider → destination. Uses routing + traffic model.

### 3.5 Pricing & Surge

Pricing multiplies a base fare by time + distance and applies a **surge multiplier** when demand
exceeds supply in a cell. Surge is computed **per cell** (per category) using the ratio of ride
requests to available drivers over a recent time window.

```
   surge_multiplier = f(requests_per_min, available_drivers, category)

   if requests >> drivers  →  multiplier goes up  →  more drivers come online
                                                   →  some riders give up
                                                   →  market rebalances

   e.g., Bengaluru Outer Ring Road at 6:30 PM (office exit):
   - AUTO: 1.2x surge
   - MICRO: 1.5x surge
   - BIKE: 1.1x surge
```

Each category can surge independently. **Upfront fares** (introduced to compete with Uber) show the
rider a fixed price before booking, computed as `estimated_distance × per_km_rate + estimated_time ×
per_min_rate × surge`, with Ola taking the risk of route deviation.

### 3.6 Ride State Machine service

Owns the authoritative state of every ride. Built on a strongly consistent store. Every state
transition is an event appended to an event log (Kafka) so downstream services can react: Payment
listens for `COMPLETED`, ETA listens for `ONGOING`, Analytics consumes everything.

### 3.7 Payment service

More complex than Uber's because of the **cash option**. Handles three flows:

- **Ola Money / UPI** — digital, automatic debit at trip end. Same as Uber's card flow but routed
  through Ola's wallet or NPCI UPI.
- **Cash** — driver collects cash; driver app records `cash_collected = true`. Ola's commission
  (typically 15–25%) is then owed **by the driver to Ola**. Nightly reconciliation tracks each
  driver's running balance; if it goes negative (driver owes Ola), the driver must settle via Ola
  Money / UPI before taking more cash rides.
- **Corporate / Ola for Business** — billed monthly to a corporate account.

The cash flow is a uniquely Indian complexity — it inverts who-owes-whom and requires a separate
**driver wallet / ledger** to track driver balances.

### 3.8 Driver lifecycle & gamification resistance

Indian drivers are notoriously creative at gaming the system: switching off GPS to avoid rides,
faking trips, accepting and cancelling selectively. Ola's driver app includes:

- **Mandatory GPS broadcasting** while online (no ping = auto-offline after 30s).
- **Acceptance rate tracking** — drivers with low acceptance get fewer ride allocations.
- **Cancellation penalties** — too many cancellations → temporary suspension.
- **Fraud detection** — same driver-rider pair repeatedly, GPS spoofing patterns, etc.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │   Rider      │     │   Driver     │     │   Vehicle        │
   │ - id         │     │ - id         │     │ - id             │
   │ - name       │     │ - name       │     │ - driver_id      │
   │ - phone      │     │ - rating     │     │ - category       │
   │ - payment    │     │ - status     │     │   (AUTO/BIKE/    │
   │   methods    │     │ - driver_    │     │    MICRO/...)    │
   └──────────────┘     │   wallet_bal │     │ - plate, model   │
                        └──────────────┘     └──────────────────┘
                                                                │
   ┌────────────────────────────────────────────────────────────┘
   │
   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                       RIDE                                    │
   │ - id, rider_id, driver_id (null until assigned)              │
   │ - category (AUTO/BIKE/MICRO/MINI/PRIME/OUTSTATION)           │
   │ - pickup_geo, pickup_landmark, dest_geo, dest_landmark       │
   │ - status (REQUESTED/ACCEPTED/ARRIVING/ONGOING/COMPLETED/     │
   │           CANCELLED/PAYMENT_PENDING/CLOSED)                  │
   │ - fare_estimate (upfront), actual_fare, surge_multiplier     │
   │ - payment_mode (OLA_MONEY/UPI/CASH/CORPORATE)                │
   │ - created_at, accepted_at, started_at, ended_at              │
   └────────────────────────────┬─────────────────────────────────┘
                                │ 1
                                │
                                ▼ *
                       ┌──────────────────┐
                       │  Ride Event      │   (event-sourced log)
                       │ - ride_id        │
                       │ - event_type     │
                       │ - lat/lng/ts     │
                       │ - payload        │
                       └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │                  DRIVER WALTER / LEDGER                      │
   │ - driver_id                                                   │
   │ - date                                                        │
   │ - rides_count, total_fare, ola_commission                    │
   │ - cash_collected, digital_collected                          │
   │ - net_balance (driver owes Ola if negative)                  │
   └──────────────────────────────────────────────────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                          | Why                                  |
| ------------------------------- | ------------------------------ | ------------------------------------ |
| Rider/Driver accounts           | MySQL/PostgreSQL (sharded)     | Strong consistency, transactional    |
| Ride records (current state)    | Strongly consistent KV / MySQL | Only one writer per ride; correctness |
| Ride events (history)           | Kafka → S3/warehouse           | Append-only, analytics-friendly      |
| Live driver locations           | In-memory geo index (Redis + custom) | Sub-second reads/writes         |
| ETA / routing graph             | In-memory graph (OSRM)         | Sub-100ms path queries               |
| Driver wallet / cash ledger     | MySQL + event log              | Auditability                         |
| Push tokens (mobile)            | Redis / DynamoDB               | Fast lookup by user_id               |
| Landmark geocoder cache         | Redis                          | Hot lookup                           |

### 4.3 Why the live location is in-memory

Driver GPS is updated every few seconds and read every time a rider opens the app. A disk-backed DB
cannot serve that read/write ratio at the required latency. The Location service keeps an in-memory
quadtree/geohash index; if a node crashes, drivers re-emit their location within seconds and the
index rebuilds.

---

## 5. Request Flow — Booking an Ola Ride

```
RIDER APP     API GW     RIDER SVC    LOCATION    DISPATCH     DRIVER APP    RIDE STATE
   │             │           │            │            │             │           │
   │─open app───▶│           │            │            │             │           │
   │             │─auth──────▶│            │            │             │           │
   │             │           │─nearby?────▶│           │             │           │
   │             │           │◀─driver set─┤ (per cat) │             │           │
   │◀──autos/bikes/cabs on map + ETA──────┤           │             │           │
   │             │           │            │            │             │           │
   │─pick AUTO, enter dest, see upfront fare                                       │
   │             │           │            │            │             │           │
   │─request ride▶│          │            │            │             │           │
   │             │─create ride ────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │             │           │            │   dispatch picks AUTO candidates ──▶│
   │             │           │            │            │             │           │
   │             │           │            │            │──offer (WS)────────────▶│
   │             │           │            │            │             │           │
   │             │           │            │            │◀─────accepts! ──────────┤
   │             │           │            │            │             │           │
   │             │           │            │            │──atomic assign─────────▶│
   │             │           │            │            │             │           │
   │◀──driver info + ETA────┤◀─────────────────────────────────────────────────┤
   │             │           │            │            │             │           │
   │  driver drives to pickup; GPS pings every 1-5s                                 │
   │             │           │            │            │             │           │
   │◀──live driver position via WebSocket─────────────────────────────────────────│
   │             │           │            │            │             │           │
   │─"arrived" (geo-fence)──────────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │─rider boards; trip ONGOING ────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │   driver drives; GPS continues; ETA recomputed                                │
   │             │           │            │            │             │           │
   │─arrive at destination ──────────────────────────────────────────────────────▶│
   │             │           │            │            │             │           │
   │             │                                                        │
   │             │      Kafka: RIDE_COMPLETED event ──────────────────▶│
   │             │                                              PAYMENT SVC │
   │             │                                              ─if Ola Money/UPI: auto-debit
   │             │                                              ─if cash: driver records cash; ledger updates
   │◀──receipt + rating prompt────────────────────────────────────────┤
```

**Step-by-step:**

1. **Rider opens app.** Calls `/nearbyVehicles?lat&lng&categories=AUTO,BIKE,MICRO`. API GW routes to
   Rider service.
2. **Rider service queries Location service** for vehicles within ~2km, **per category**. Returns
   counts + positions for autos, bikes, cabs. App plots them on the map.
3. **Rider selects AUTO, enters destination.** Pricing service computes upfront fare
   (`distance × per_km + time × per_min × surge`), surge read from the cell's cache.
4. **Rider confirms.** A `Ride` is created in `REQUESTED` state with the chosen category and
   upfront fare.
5. **Dispatch picks AUTO candidates** from Location service (filtered by category, driver rating,
   recent cancellation history).
6. **Offer broadcast** to candidates over WebSocket. Each driver app shows the offer (pickup,
   dest, ETA, fare).
7. **First driver to accept wins.** Driver app sends `ACCEPT` with `ride_id`. Ride State service
   does a conditional update — if `status == REQUESTED`, set `driver_id = X` and `status =
   ACCEPTED`. Other drivers get a "ride taken" push.
8. **Rider is notified** of the matched driver, vehicle, plate, ETA.
9. **Driver drives to pickup.** GPS pings update Location service; rider app receives position
   updates over WebSocket.
10. **Arrival detected** (geo-fence: driver within ~50m of pickup). Ride transitions to `ARRIVING`
    → rider gets a push.
11. **Trip starts.** Driver presses "Start ride"; ride → `ONGOING`. ETA service recomputes trip ETA
    as driver moves.
12. **Trip ends.** Driver presses "End ride" near destination; ride → `COMPLETED`. A
    `RIDE_COMPLETED` event lands on Kafka.
13. **Payment service** consumes the event. If Ola Money/UPI → auto-debit. If cash → driver app
    prompts driver to confirm cash collected; ledger updates driver's balance.
14. **Rating prompt** to both rider and driver.

---

## 6. Scaling Strategy

### 6.1 Shard by city

Ola's traffic is geographically clustered. A rider in Bengaluru never shares state with a driver in
Delhi. Ola shards the Location service and Dispatch by **city / region**, so each shard handles only
its own geo cells.

### 6.2 In-memory geo index

The Location service is the hottest path. It's in-memory (Redis + custom quadtree), sharded by geo
cell, stateless above the index — a dead node is rebuilt from the next round of GPS pings within
seconds.

### 6.3 Event-driven downstream services

Payment, Analytics, Receipts, Fraud all consume from Kafka. They never participate in the critical
dispatch path, so a Payment outage doesn't break ride booking.

### 6.4 Idempotent writes

Every API call carries an idempotency key. If a rider's flaky network causes a double-tap "request
ride", the backend deduplicates. Payment uses exactly-once semantics via idempotency keys.

### 6.5 WebSocket fan-out for driver push

Each driver app holds a WebSocket to a **connection server**. When Dispatch wants to ping 5 drivers,
it sends 5 messages to the connection servers responsible for those drivers. The connection server
layer is scaled horizontally; hundreds of thousands of open sockets are normal in a metro.

### 6.6 Multi-category dispatch

Because the same driver pool serves multiple categories (some drivers are eligible for both Micro
and Mini), dispatch must respect category eligibility. This is encoded as a filter in the candidate
query.

### 6.7 Multi-region + disaster recovery

Ola runs multiple datacenters (AWS India + on-prem). Consistency for ride state is achieved via
consensus within a region. Cross-region failover is practised but rare (data residency under Indian
law favours in-country hosting).

### 6.8 Offline-first driver app

Indian driver networks are flaky. The driver app queues GPS pings and ride events locally
(SQLite on-device) and replays them on reconnection. This is critical — without it, drivers would
lose rides every time they entered a network dead zone (common in basements, toll plazas, rural
stretches).

---

## 7. Tech Stack

| Layer                       | Technology                                            |
| --------------------------- | ----------------------------------------------------- |
| Mobile apps                 | Kotlin (Android), Swift (iOS), native architectures   |
| Backend languages           | Java (Spring Boot), Go, Python, Node.js (legacy)      |
| API gateway                 | Custom, on top of NGINX / Envoy                        |
| Databases                   | MySQL, PostgreSQL, Cassandra                           |
| In-memory                   | Redis                                                 |
| Streaming                   | Apache Kafka                                          |
| Service mesh                | Custom / Istio                                         |
| Geospatial                  | Custom quadtree + Geohash; OSRM / custom routing      |
| ML / ETA / pricing          | Python, XGBoost / LightGBM                             |
| Maps                        | MapMyIndia / OpenStreetMap / Google Maps (hybrid)     |
| Deployment                  | Kubernetes, Docker, Spinnaker / Jenkins                |
| Observability               | Prometheus + Grafana, ELK, Jaeger tracing             |
| Cloud                       | AWS India + on-prem datacenters                        |

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

Use **Redis GEO** commands (`GEOADD`, `GEORADIUS` / `GEOSEARCH`), which internally use geohash
sorted sets.

```python
# driver pings location with category
redis.geoadd("drivers:AUTO", lng, lat, driver_id)
redis.geoadd("drivers:BIKE", lng, lat, driver_id_bike)

# rider asks for nearby autos
nearby_autos = redis.geosearch(
    "drivers:AUTO",
    longitude=rider_lng, latitude=rider_lat,
    radius=2, unit="km",
    withcoord=True, count=20
)
```

That's the core of Ola's location lookup, in two Redis calls — one per category.

### 8.3 The dispatch logic

```python
# rider requests an AUTO ride
candidates = redis.geosearch("drivers:AUTO", ...)

# broadcast offer to each candidate driver via Socket.io
for driver_id in candidates:
    socketio.emit("ride_offer", offer_payload, to=driver_id)

# first driver to accept wins (atomic)
@socketio.on("accept_ride")
def on_accept(ride_id, driver_id):
    rows = db.execute("""
        UPDATE rides
        SET driver_id = %s, status = 'ACCEPTED'
        WHERE id = %s AND status = 'REQUESTED'
        RETURNING id
    """, (driver_id, ride_id))
    if rows:  # this driver won the race
        socketio.emit("ride_accepted", {...}, to=rider_id)
        socketio.emit("ride_taken", {...}, to=other_drivers)
```

### 8.4 ETA

Use **OSRM** (Open Source Routing Machine). Or call the free OSRM API:

```python
import requests
r = requests.get(f"http://router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}")
duration = r.json()["routes"][0]["duration"]  # seconds
```

For India-specific routing, MapMyIndia offers a paid API with better local road data.

### 8.5 Upfront fare

```python
base_fare = 30  # ₹
per_km = 12
per_min = 1
surge = get_surge(cell_id, category)  # from Redis

upfront_fare = (base_fare
                + estimated_km * per_km
                + estimated_min * per_min) * surge
```

### 8.6 Cash payment ledger

```python
def record_cash_payment(driver_id, ride_id, fare, ola_commission_pct=0.20):
    commission = int(fare * ola_commission_pct)
    driver_earnings = fare - commission
    with db.transaction():
        db.execute("UPDATE rides SET payment_mode='CASH', actual_fare=%s WHERE id=%s",
                   (fare, ride_id))
        # driver wallet: positive = Ola owes driver; negative = driver owes Ola
        db.execute("""INSERT INTO driver_wallet (driver_id, date, cash_collected,
                        ola_commission, net_balance)
                      VALUES (%s, %s, %s, %s, %s)""",
                   (driver_id, today, fare, commission, driver_earnings - commission))
```

### 8.7 What you'll learn

- How geospatial indexing turns O(N) into O(local).
- How atomic conditional updates solve "only one rider wins" races.
- How multi-category dispatch filters candidate pools.
- How cash payments invert the money flow and require a driver-side ledger.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered        | Why Ola chose it                                             |
| ----------------------------------------------- | ----------------------------- | ------------------------------------------------------------ |
| **Multi-modal (Auto + Bike + Cab) in one app**  | Separate apps per mode        | Indian riders mix modes; one app = more bookings per user    |
| **Cash payments supported**                     | Digital-only                  | Most Indians paid cash in 2010; cash grew the network        |
| **In-memory geo index (Redis + quadtree)**      | PostGIS on Postgres           | Sub-millisecond reads/writes for millions of pings           |
| **Shard by city/region**                        | Global sharding               | Geographic locality; fault isolation per city                |
| **Broadcast dispatch (ping many, first wins)**  | Single assign + confirm       | Higher acceptance rate; drivers self-select                  |
| **Upfront fares**                               | Post-trip metered fare        | rider transparency; competitive with Uber                    |
| **Offline-first driver app (local queue)**      | Online-only                   | Indian networks are flaky; offline queue saves rides         |
| **Per-category surge**                          | Global surge per city         | Each mode has its own supply/demand curve                    |

### The deepest trade-off

**Cash payments vs. digital friction.** Supporting cash made Ola ubiquitous in India — drivers
without bank accounts could participate, riders without cards could pay. But it created a uniquely
Indian complexity: Ola must track **each driver's running balance** (cash-collected minus
commission), chase drivers who owe money, and reconcile nightly. A digital-only model (Uber's
original approach) is operationally far simpler but would have excluded most of Ola's early market.
Ola chose market reach over operational simplicity — a deliberate India-first decision.

---

## 10. Common Interview Questions

**Q1: How would you design Ola?**
Start with the two-sided marketplace. Split into rider/driver/dispatch. Explain the ride state
machine. Highlight that dispatch must be atomic (only one rider wins a driver). Discuss Location
service with geohash/quadtree. Mention the multi-category and cash-payment India specifics.

**Q2: How do you find nearby drivers quickly, per category?**
Geohash or quadtree index in memory, **keyed by category** (separate Redis GEO sets per category).
Drivers write GPS every few seconds; rider query is a radial search over the cell and neighbors for
the requested category.

**Q3: How do you prevent two riders from getting the same driver?**
Conditional update: `UPDATE rides SET driver_id=? WHERE id=? AND status='REQUESTED'`. Only one write
succeeds. Or a distributed lock with short TTL.

**Q4: How does surge pricing work?**
Per-cell, per-category ratio of requests to available drivers over a time window. Multiplier is
published to a cache; rider app reads it.

**Q5: How do you handle cash payments?**
Driver collects cash; driver app records `cash_collected`. Ola's commission is owed by the driver.
Nightly reconciliation tracks each driver's wallet balance; drivers with negative balances must
settle via Ola Money/UPI before taking more cash rides.

**Q6: How do you scale to millions of drivers?**
Shard by city. In-memory geo index. WebSocket fan-out via connection servers. Event log for
downstream consumers. Stateless services above the geo index.

**Q7: Why an event-sourced ride state?**
Decouples fast path (dispatch) from slow path (payment, analytics, fraud). Enables replay for
debugging. Audit trail for disputes ("the driver took a longer route").

**Q8: What happens if the Location service goes down?**
Drivers re-emit GPS within seconds. The index rebuilds from incoming pings. Services are sharded per
city, so a Bengaluru outage doesn't affect Delhi.

**Q9: How do you handle a driver going offline mid-ride?**
Timeout + heartbeat. If no GPS ping for N seconds, driver is marked offline; if mid-ride, support is
alerted and the ride may be reassigned. Rider gets a push.

**Q10: How do you handle flaky driver networks (Indian reality)?**
Offline-first driver app: queues GPS pings and ride events in local SQLite, replays on reconnection.
Without this, drivers would lose rides every time they hit a network dead zone.

**Q11: How do upfront fares work — who takes the route-deviation risk?**
Ola computes an estimate before booking. If the actual route is longer (traffic, rider asks for a
detour), Ola absorbs the difference for the rider (the driver is paid for actual distance). This is a
pricing risk Ola manages with ML route estimates.

---

## Further reading

- Ola Engineering Blog (medium.com/olabusiness) — driver lifecycle, dispatch, scaling.
- Uber Engineering Blog (eng.uber.com) — for the broader ride-hailing patterns Ola shares.
- "How Ola scaled to X" conference talks (AWS re:Invent, RedisConf).
- NPCI / UPI docs — for Ola Money's UPI integration.
- OpenStreetMap India + MapMyIndia — for India-specific routing.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

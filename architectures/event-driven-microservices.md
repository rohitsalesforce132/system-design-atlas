# Event-Driven Microservices — Online Food Delivery Platform

> How to design a Zomato/Swigty-scale food delivery platform from scratch. This is the architecture that processes "I'm hungry → tap → food arrives in 30 minutes" — across 5+ independently deployable services coordinated by Kafka events, with saga orchestration for distributed transactions, CQRS for fast reads, and event sourcing for a complete audit trail.

---

## Table of Contents

1. [Problem Statement & Requirements](#1-problem-statement--requirements)
2. [Capacity Estimation](#2-capacity-estimation)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Component Selection](#4-component-selection)
5. [Database Schema](#5-database-schema)
6. [API Design](#6-api-design)
7. [Step-by-Step Request Flow](#7-request-flow)
8. [Scaling Strategy](#8-scaling-strategy)
9. [Failure Modes & Mitigation](#9-failure-modes)
10. [Trade-off Analysis](#10-trade-off-analysis)

---

<a id="1-problem-statement--requirements"></a>
## 1. Problem Statement & Requirements

### The Problem in Plain English

A customer opens the app, browses restaurants nearby, picks items, places an order, pays, watches a live map of the rider heading to the restaurant and then to their door, and gets food in ~30 minutes. Behind that simple flow, **five different systems must coordinate**: the restaurant must accept the order, a rider must be assigned, the kitchen must cook, the rider must pick up, and the payment must be captured — and if any one of them fails, the others must cleanly unwind.

This is fundamentally harder than "one app talking to one database." Each subsystem has a different owner, a different scale profile, and different failure modes. The kitchen POS shouldn't crash because the payment gateway had a hiccup. The rider app shouldn't care which database the order service uses.

### Analogy: The Restaurant Kitchen Ticket System

```
Think of a busy restaurant kitchen:

  Waiter writes order on a ticket → puts ticket on the rail (Kafka topic)
    → Chef sees ticket → starts cooking (restaurant-accept service)
    → Expediter assigns runner (rider-assignment service)
    → Runner picks up food (pickup service)
    → Cashier rings up payment (payment service)

Nobody yells across the kitchen. Nobody waits for someone else to finish
before starting their part. The TICKET is the single source of truth —
everyone reads it, does their job, marks their step done.

If the runner quits mid-shift → the expediter reassigns.
If the stove breaks → the chef tears up the ticket and the cashier refunds.

That ticket rail is Kafka.
The expediter is the Saga Orchestrator.
The torn-ticket-and-refund is a compensating transaction.
```

### Functional Requirements

| # | Requirement | Description |
|---|-------------|-------------|
| F1 | Browse & search | Customer searches restaurants by cuisine/location, sees menus with live availability |
| F2 | Cart & checkout | Build cart, apply coupons, choose address, see delivery fee + ETA |
| F3 | Place order | Submit order → restaurant gets notified → accepts/rejects within timeout |
| F4 | Payment | Charge customer (UPI/card/wallet), refund on cancellation |
| F5 | Rider assignment | Find nearest available rider, assign, track acceptance |
| F6 | Real-time tracking | Customer sees rider location on map, live status updates |
| F7 | Order lifecycle | Placed → Accepted → Preparing → Picked Up → Delivered (or Cancelled at any step) |
| F8 | Notifications | Push/SMS to customer, restaurant, and rider at each state change |
| F9 | Ratings | Post-delivery rating for restaurant and rider |

### Non-Functional Requirements

| # | Requirement | Target |
|---|-------------|--------|
| NF1 | Availability | 99.95% (≈ 22 min downtime/month) — lunch rush is sacred |
| NF2 | Order placement latency | p99 < 500ms (write path), p99 < 100ms (read path) |
| NF3 | Tracking update latency | Rider position → customer screen < 2s |
| NF4 | Throughput | 50K orders/hour peak (lunch + dinner) |
| NF5 | Durability | No order ever silently lost — every event persisted before ack |
| NF6 | Consistency | Eventual consistency between services; **strong** within order state machine |
| NF7 | Fault isolation | Payment outage must NOT block order placement (queue it) |
| NF8 | Auditability | Complete event log — reconstruct any order's history from events |

### Out of Scope (for this design)

- Restaurant onboarding / menu management UI
- Rider payroll and shift management
- ML-based ETA prediction model training
- Marketing campaigns / loyalty programs

---

<a id="2-capacity-estimation"></a>
## 2. Capacity Estimation

We'll size for a mid-sized food delivery platform in one country (think Swiggy India scale, not global).

### 2.1 User Base & Order Volume

```
Assumptions:
  - 50M registered customers, 10M monthly active
  - 500K restaurants onboarded, 200K active daily
  - 1M riders on platform, 300K active during peak

Daily orders:
  - Average day:    3M orders/day
  - Peak (weekend): 5M orders/day

Peak hour (lunch 12:00-13:00 or dinner 19:00-20:00):
  - 40% of daily orders in 2 peak hours
  - 5M × 0.40 / 2 = 1M orders/hour = ~278 orders/second (peak)
```

### 2.2 Request Breakdown (Read vs Write)

```
For every 1 order placed, the user performs:
  - ~200 menu/restaurant reads (browsing)         → 278 × 200 = 55,600 reads/s peak
  - ~5 cart operations                            → 278 × 5   = 1,390 writes/s
  - 1 order placement                             → 278 writes/s
  - 1 payment                                     → 278 writes/s

Read : Write ratio ≈ 100 : 1  (heavy read skew — typical for browse-then-buy)

Peak QPS summary:
  - Read QPS (menu/restaurant):  ~55,000/s
  - Write QPS (orders):           ~280/s
  - Tracking updates:             see below
```

### 2.3 Real-Time Tracking (The Spike)

```
Each active order has a rider pinging location every 5 seconds.
Concurrent active orders at peak ≈ 1M orders/hour × 0.5h avg delivery = 500K

  Rider location updates: 500,000 riders / 5s = 100,000 updates/second
  Customer tracking polls: 500,000 customers × 1 poll/3s = 167,000 reads/s

This is the single biggest load on the system — bigger than the order writes
by 350×. This is why tracking gets its own dedicated service + WebSocket infra.
```

### 2.4 Storage Estimation

```
Per order record:
  - Order row (JSON):      ~2 KB
  - Order items (avg 3):   ~1 KB
  - Events (avg 12/order): ~12 × 0.5 KB = 6 KB
  - Tracking points:       ~180 points × 100B = 18 KB  (30 min × 6/min)
  Total per order:         ~27 KB

Daily storage (orders only):
  5M orders × 27 KB = 135 GB/day

Annual: 135 GB × 365 ≈ 49 TB/year of order + event data

Plus Kafka retention (7-day hot retention):
  Event throughput: 280 orders/s × 12 events × 0.5 KB = 1.68 MB/s
  7-day Kafka retention: 1.68 MB/s × 604,800s ≈ 1.0 TB in Kafka topics
```

### 2.5 Bandwidth

```
Outbound (API responses to customers):
  55,000 reads/s × 4 KB avg response = 220 MB/s = 1.76 Gbps

Tracking fanout (WebSocket):
  500K concurrent connections × 0.5 KB update every 3s
  = 83 MB/s = 660 Mbps continuous broadcast

Inbound (rider location ingestion):
  100,000 updates/s × 100B = 10 MB/s = 80 Mbps
```

### 2.6 Compute (Rough Service Sizing)

```
Order Service:      280 orders/s, ~20ms CPU each → ~6 cores busy → 20 cores w/ headroom
Menu/Read Service:  55,000 reads/s, ~2ms each    → ~110 cores busy → 300 cores w/ replicas
Tracking Service:   100K writes/s ingest + 500K WS conns → WebSocket-heavy, 50 nodes
Rider Assignment:   278/s but each is expensive (geo-query) → 100 cores
Payment Service:    280/s, mostly I/O bound (gateway calls) → 30 cores
Kafka brokers:      1.68 MB/s × 3 replication = 5 MB/s → 6 brokers easily
```

**Bottom line:** ~600–800 CPU cores, ~50 TB/year storage, ~3 Gbps peak bandwidth. Comfortably mid-size — this is not Google-scale, but the *coordination complexity* is the hard part, not raw throughput.

---

<a id="3-high-level-architecture"></a>
## 3. High-Level Architecture

### 3.1 The Big Picture

```
                              ┌──────────────────────────────────────┐
                              │           CUSTOMER APP (iOS/Android) │
                              │  Browse → Cart → Order → Track → Rate│
                              └───────────────┬──────────────────────┘
                                              │ HTTPS + WebSocket
                                              ▼
                              ┌──────────────────────────────────────┐
                              │              API GATEWAY              │
                              │  (Kong/Envoy: auth, rate-limit, TLS)  │
                              │  Routes to services, holds WS conns   │
                              └──────┬──────────┬──────────┬─────────┘
                                     │          │          │
                         ┌───────────▼┐ ┌───────▼────────┐ │
                         │  Read API  │ │  Order Command │ │
                         │  (CQRS Q)  │ │  API (CQRS C)  │ │
                         │  Elasticsearch│ │              │ │
                         └──────┬─────┘ └───────┬────────┘ │
                                │               │          │
                                │               ▼          │
                                │      ┌────────────────┐  │
                                │      │ ORDER SERVICE  │  │
                                │      │ (Saga          │◄─┘
                                │      │  Orchestrator) │
                                │      └───────┬────────┘
                                │              │ publishes events
                                │              ▼
                                │   ┌────────────────────────────────────┐
                                │   │           KAFKA EVENT BUS           │
                                │   │  topics: order.created,             │
                                │   │  restaurant.accepted, payment.      │
                                │   │  captured, rider.assigned,          │
                                │   │  order.delivered, order.cancelled   │
                                │   └─┬──────┬──────┬──────┬──────┬───────┘
                                │     │      │      │      │      │
                                │     ▼      ▼      ▼      ▼      ▼
                                │   ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐
                                │   │REST│ │PAY │ │RIDER│ │NOTIF│ │TRACK│
                                │   │SVC │ │SVC │ │ASSN │ │ SVC │ │ SVC │
                                │   │    │ │    │ │ SVC │ │     │ │     │
                                │   └─┬──┘ └─┬──┘ └─┬──┘ └────┘ └──┬─┘
                                │     │      │      │               │
                                └─────┘      │      │               │
                                  (reads)    │      │               │
                                             ▼      ▼               ▼
                          ┌──────────────────────────────────────────────┐
                          │              DATA TIER                         │
                          │  ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
                          │  │PostgreSQL│ │PostgreSQL│ │  Redis        │ │
                          │  │Orders DB │ │Riders DB │ │  (geo-index,  │ │
                          │  │(write)   │ │(write)   │ │   cache, WS)  │ │
                          │  └────┬─────┘ └──────────┘ └───────────────┘ │
                          │       │                                       │
                          │  ┌────▼─────────────────────────────────────┐│
                          │  │  Event Store (Kafka + S3 archival)       ││
                          │  │  ← source of truth for order history      ││
                          │  └──────────────────────────────────────────┘│
                          │  ┌──────────────────────────────────────────┐│
                          │  │  Read Models (Elasticsearch, Redis)      ││
                          │  │  ← projections rebuilt from events        ││
                          │  └──────────────────────────────────────────┘│
                          └──────────────────────────────────────────────┘

                              ┌──────────────────────────────────────┐
                              │      RESTAURANT POS / TABLET         │
                              │  (Receives order, accepts, marks ready)│
                              └──────────────────────────────────────┘
                              ┌──────────────────────────────────────┐
                              │         RIDER APP                    │
                              │  (Receives assignment, pings GPS,     │
                              │   marks picked up / delivered)        │
                              └──────────────────────────────────────┘
```

### 3.2 The Three Patterns That Make This Work

This isn't just "microservices with a queue." Three patterns carry the load:

```
┌─────────────────────────────────────────────────────────────────────┐
│  PATTERN 1: SAGA (distributed transactions without 2PC)             │
│                                                                     │
│  Problem: Order placement touches 4 services. If payment fails      │
│  after the restaurant already accepted, you can't "ROLLBACK"        │
│  across separate databases.                                         │
│                                                                     │
│  Solution: A sequence of local transactions, each with a            │
│  COMPENSATING action that undoes it if a later step fails.          │
│                                                                     │
│    Order Created                                                    │
│      → Reserve Inventory (restaurant accepts)                       │
│         → Charge Payment                                            │
│            → Assign Rider                                           │
│               → SUCCESS                                             │
│            ← (rider fails) → Retry / re-assign                      │
│         ← (payment fails) → Release Inventory (reject order)        │
│      ← (restaurant rejects) → Cancel Order                          │
│                                                                     │
│  Orchestrated by the Order Service publishing commands and          │
│  listening for success/failure events.                              │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  PATTERN 2: CQRS (Command Query Responsibility Segregation)         │
│                                                                     │
│  Problem: The order DB is optimized for writes (append events).     │
│  But customers need fast reads: "show my active orders," "where's   │
│  my rider?" Different access patterns fight each other.             │
│                                                                     │
│  Solution: Split writes from reads into separate models.            │
│                                                                     │
│    WRITE SIDE (Commands)        READ SIDE (Queries)                 │
│    Order Service                Elasticsearch / Redis               │
│    ↓ publishes events           ↑ consumes events                   │
│    Event Store (Kafka)          → builds optimized read views       │
│                                                                     │
│  Write model = append-only event log (source of truth).             │
│  Read models = denormalized projections rebuilt from the log.       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  PATTERN 3: EVENT SOURCING (the log IS the state)                   │
│                                                                     │
│  Problem: When something goes wrong ("why was this order            │
│  cancelled?"), a single mutable row tells you the END state but     │
│  not the JOURNEY. You need an audit trail.                          │
│                                                                     │
│  Solution: Don't store "current state." Store every state change    │
│  as an immutable event. Current state = replay of all events.       │
│                                                                     │
│    order.created     {orderId: 1, items: [...], total: 450}        │
│    restaurant.accept {orderId: 1, at: 12:01}                       │
│    payment.captured  {orderId: 1, txnId: x, amount: 450}           │
│    rider.assigned    {orderId: 1, riderId: 99}                     │
│    order.cancelled   {orderId: 1, reason: "rider_no_show"}         │
│                                                                     │
│  Replay these 5 events → you know EXACTLY what happened.            │
│  This is your audit log, your bug-repro dataset, and your           │
│  read-model rebuild source — all in one.                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.3 Service Inventory

| Service | Owns | DB | Scale Driver |
|---------|------|----|--------------| 
| Order Service | Order state machine, saga orchestration | PostgreSQL (write) + Kafka (event log) | Orders/sec |
| Restaurant Service | Menu, inventory, accept/reject | PostgreSQL | Reads (menu browse) |
| Payment Service | Charges, refunds, gateway calls | PostgreSQL | Orders/sec (I/O bound) |
| Rider Assignment | Geo-match riders to orders | Redis GEO + PostgreSQL | Orders/sec + geo queries |
| Tracking Service | Live rider GPS, WebSocket fanout | Redis (hot) + ClickHouse (cold) | Concurrent riders |
| Notification Service | Push/SMS/Email fanout | None (stateless fan-out) | Events/sec |
| Read API / CQRS Query | Serves customer-facing reads | Elasticsearch + Redis | Read QPS |

---

<a id="4-component-selection"></a>
## 4. Component Selection

### 4.1 Event Backbone — Apache Kafka

**Why Kafka (not RabbitMQ / SQS / NATS):**

```
┌──────────────────┬─────────────────────┬──────────────────┬────────────────┐
│ Need             │ Kafka               │ RabbitMQ          │ SQS             │
├──────────────────┼─────────────────────┼──────────────────┼────────────────┤
│ Event sourcing   │ ✓ (log, replayable) │ ✗ (queue, delete)│ ✗              │
│ Multiple consumers│ ✓ (consumer groups)│ ✓ (fanout)       │ ✗ (one consumer)│
│ Throughput       │ millions/sec        │ ~50K/sec          │ high but limits │
│ Replay history   │ ✓ (offset seek)     │ ✗                 │ ✗              │
│ Ordering         │ per-partition        │ per-queue         │ FIFO (limited)  │
│ Retention        │ days–years          │ until acked       │ until acked     │
└──────────────────┴─────────────────────┴──────────────────┴────────────────┘
```

Event sourcing **requires** an append-only log that can be replayed. Kafka is literally a distributed commit log — that's its core abstraction. Queues (RabbitMQ/SQS) delete messages after ack, so they cannot serve as the event store.

**Configuration:**
- 6 brokers, replication factor 3
- `order-events` topic: 32 partitions (keyed by `orderId` for ordering)
- 7-day retention (hot replay window) + S3 archival for cold storage
- `tracking-events` topic: 64 partitions (keyed by `riderId`)

### 4.2 Order DB — PostgreSQL

**Why PostgreSQL (not MongoDB/Cassandra):**

The Order Service holds the saga orchestrator's authoritative state. Orders have strong relationships (order → items → events → payments), ACID guarantees matter (you don't double-charge), and the write volume is modest (280/sec). PostgreSQL's MVCC, mature replication (logical + physical), and JSONB (for flexible event payloads) make it ideal.

Cassandra would be overkill (no need for multi-region writes yet) and MongoDB's lack of multi-document transactions pre-4.0 was a dealbreaker — even now, PG's transaction semantics are stronger.

### 4.3 Read Models — Elasticsearch + Redis

```
Elasticsearch:  for "show me restaurants near X serving cuisine Y" 
                 (full-text + geo + filtering — its sweet spot)
                 Alternative considered: PostgreSQL GIST — but ES handles 
                 faceted search and ranking better at 55K reads/s.

Redis:          for "show my active orders" and rider location hot-cache
                 Alternative considered: memcached — but Redis has GEO 
                 commands (GEORADIUS) we need for rider matching anyway.
```

### 4.4 API Gateway — Kong

Kong (or Envoy) handles: TLS termination, JWT auth, per-customer rate limiting, request routing, and **holds the WebSocket connections** for live tracking (so backend services don't have to). Alternatives: AWS API Gateway (managed but WebSocket pricing is per-connection-minute and gets expensive at 500K concurrent), Envoy (more flexible, steeper ops curve).

### 4.5 Service-to-Service Communication

```
Synchronous:  gRPC (for saga commands that need a response)
              - Order → Restaurant: "accept this order?" (needs yes/no)
              - Order → Payment: "charge this?" (needs success/fail)
              Why gRPC: typed contracts (.proto), low latency, connection pooling.

Asynchronous: Kafka events (for fire-and-forget + fanout)
              - Order publishes order.created → Notification + Tracking both react
              - Why events: decouples producer from consumers, scales independently.
```

The rule: **if the caller needs the answer to proceed, use gRPC; if it's just announcing something happened, use Kafka.**

### 4.6 Deployment — Kubernetes

Each service is a Deployment with HPA (Horizontal Pod Autoscaler) keyed on CPU + custom metrics (Kafka consumer lag). Kafka itself runs on Strimzi (K8s-native operator) or managed (MSK/Confluent Cloud). Service mesh: Linkerd (lighter) or Istio (heavier but more features) — optional, start without it.

---

<a id="5-database-schema"></a>
## 5. Database Schema

### 5.1 Order Service (PostgreSQL) — Write Model

```sql
-- The current-state table (for quick lookups; the event log is source of truth)
CREATE TABLE orders (
    order_id        BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    restaurant_id   BIGINT NOT NULL,
    status          VARCHAR(32) NOT NULL,  -- PENDING_ACCEPT, ACCEPTED, PREPARING,
                                           -- AWAITING_PICKUP, PICKED_UP, DELIVERED,
                                           -- CANCELLED, REFUNDED
    total_amount    DECIMAL(10,2) NOT NULL,
    delivery_fee    DECIMAL(10,2) NOT NULL,
    delivery_address JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    saga_id         UUID NOT NULL,         -- correlates all events in this saga
    version         INT NOT NULL DEFAULT 1  -- optimistic concurrency
);

CREATE INDEX idx_orders_customer ON orders(customer_id, created_at DESC);
CREATE INDEX idx_orders_restaurant ON orders(restaurant_id, status);
CREATE INDEX idx_orders_saga ON orders(saga_id);

CREATE TABLE order_items (
    item_id     BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(order_id),
    menu_item_id BIGINT NOT NULL,
    name        VARCHAR(255) NOT NULL,    -- snapshot at order time
    price       DECIMAL(10,2) NOT NULL,   -- snapshot at order time
    quantity    INT NOT NULL
);

CREATE INDEX idx_order_items_order ON order_items(order_id);

-- Event sourcing: the append-only log (LOCAL copy for the Order service)
-- Kafka is the authoritative event store; this table is a synchronous mirror
-- used for transactional consistency with the orders table.
CREATE TABLE order_events (
    event_id    BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(order_id),
    event_type  VARCHAR(64) NOT NULL,     -- ORDER_CREATED, RESTAURANT_ACCEPTED, ...
    payload     JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    saga_id     UUID NOT NULL
);

CREATE INDEX idx_order_events_order ON order_events(order_id, occurred_at);
```

**Key design choices:**
- `name` and `price` are **snapshotted** into `order_items` — if the restaurant changes its menu price tomorrow, yesterday's order doesn't change.
- `version` enables optimistic concurrency — if two saga steps try to update the same order, one wins, one retries.
- `saga_id` lets you trace an entire distributed transaction across services and the event log.

### 5.2 Restaurant Service (PostgreSQL)

```sql
CREATE TABLE restaurants (
    restaurant_id   BIGSERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    cuisine_type    VARCHAR(64) NOT NULL,
    location        GEOGRAPHY(POINT, 4326),  -- PostGIS
    is_active       BOOLEAN DEFAULT TRUE,
    rating          DECIMAL(2,1) DEFAULT 0
);
CREATE INDEX idx_restaurants_geo ON restaurants USING GIST(location);
CREATE INDEX idx_restaurants_cuisine ON restaurants(cuisine_type);

CREATE TABLE menu_items (
    menu_item_id    BIGSERIAL PRIMARY KEY,
    restaurant_id   BIGINT NOT NULL REFERENCES restaurants(restaurant_id),
    name            VARCHAR(255) NOT NULL,
    price           DECIMAL(10,2) NOT NULL,
    is_available    BOOLEAN DEFAULT TRUE  -- toggled by restaurant in real-time
);
CREATE INDEX idx_menu_items_restaurant ON menu_items(restaurant_id, is_available);
```

### 5.3 Rider Service (PostgreSQL + Redis GEO)

```sql
CREATE TABLE riders (
    rider_id     BIGSERIAL PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,
    phone        VARCHAR(32) UNIQUE NOT NULL,
    status       VARCHAR(16) NOT NULL,  -- AVAILABLE, BUSY, OFFLINE
    current_zone VARCHAR(32)            -- geo-zone for assignment
);
CREATE INDEX idx_riders_status_zone ON riders(status, current_zone);

CREATE TABLE rider_assignments (
    assignment_id BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL,
    rider_id      BIGINT NOT NULL,
    assigned_at   TIMESTAMPTZ DEFAULT NOW(),
    status        VARCHAR(32) NOT NULL,  -- OFFERED, ACCEPTED, REJECTED, EXPIRED
    UNIQUE(order_id, rider_id)
);
```

Redis GEO for live rider location (sub-millisecond radius queries):

```
# Rider location in Redis GEO set
GEOADD rider_locations:zone_north 77.5946 12.9716 "rider:42"
GEORADIUS rider_locations:zone_north 77.5946 12.9716 2 km COUNT 10 ASC
# → returns 10 nearest available riders within 2km, sorted by distance
```

### 5.4 Payment Service (PostgreSQL)

```sql
CREATE TABLE payments (
    payment_id   BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL,
    customer_id  BIGINT NOT NULL,
    amount       DECIMAL(10,2) NOT NULL,
    method       VARCHAR(32) NOT NULL,  -- UPI, CARD, WALLET
    status       VARCHAR(32) NOT NULL,  -- PENDING, CAPTURED, FAILED, REFUNDED
    gateway_txn_id VARCHAR(128),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(order_id)  -- one payment per order
);
CREATE INDEX idx_payments_order ON payments(order_id);
```

### 5.5 Read Model (Elasticsearch) — Projection from Events

```json
// Index: active_orders (for "my orders" screen)
{
  "orderId": 1,
  "customerId": 1001,
  "restaurantName": "Pizza Hut Indiranagar",  // denormalized for fast read
  "status": "PICKED_UP",
  "riderName": "Ramesh K.",
  "riderLocation": {"lat": 12.97, "lng": 77.59},
  "etaMinutes": 8,
  "itemsSummary": "2x Margherita, 1x Coke",
  "totalAmount": 450,
  "updatedAt": "2026-07-26T12:15:00Z"
}
```

This projection is **rebuilt from Kafka events** by a consumer. If it gets corrupted, drop the index and replay — no data loss because the event log is the source of truth.

---

<a id="6-api-design"></a>
## 6. API Design

### 6.1 REST Endpoints (Customer-Facing)

```
# Browse
GET  /api/v1/restaurants?lat=12.97&lng=77.59&cuisine=italian
     → 200: [{ id, name, cuisine, rating, etaMinutes, logoUrl }]

GET  /api/v1/restaurants/{id}/menu
     → 200: { categories: [{ name, items: [{ id, name, price, available }] }] }

# Cart (client-side; server validates at checkout)
POST /api/v1/cart/validate
     Body: { restaurantId, items: [{itemId, qty}] }
     → 200: { valid, total, deliveryFee, etaMinutes }

# Order
POST /api/v1/orders
     Body: { restaurantId, items, addressId, paymentMethod, couponCode? }
     → 202: { orderId, sagaId, status: "PENDING_ACCEPT" }
     (Order placement is async — returns immediately, saga runs via events)

GET  /api/v1/orders/{id}
     → 200: { id, status, items, rider?, eta, trackingUrl? }

GET  /api/v1/orders/active
     Query: customerId (from JWT)
     → 200: [{ orderId, status, restaurantName, eta }]

# Real-time tracking (WebSocket upgrade)
WS   /api/v1/orders/{id}/track
     → Server pushes: { riderLocation, status, eta } every 3s
```

### 6.2 Restaurant-Facing (gRPC)

```protobuf
service RestaurantOrderService {
  rpc AcceptOrder(AcceptOrderRequest) returns (AcceptOrderResponse);
  rpc RejectOrder(RejectOrderRequest) returns (RejectOrderResponse);
  rpc MarkReady(MarkReadyRequest) returns (MarkReadyResponse);
}

message AcceptOrderRequest {
  string order_id = 1;
  int32 prep_time_minutes = 2;  // restaurant estimates
}
```

### 6.3 Event Schemas (Kafka — Avro)

```json
// Topic: order-events (key = orderId)
{
  "event_id": "uuid",
  "event_type": "ORDER_CREATED",
  "order_id": 12345,
  "saga_id": "uuid",
  "customer_id": 1001,
  "restaurant_id": 88,
  "items": [{"menu_item_id": 5, "qty": 2}],
  "total_amount": 450.00,
  "occurred_at": "2026-07-26T12:00:00Z",
  "version": 1
}

// Event types emitted by the saga:
//   ORDER_CREATED
//   RESTAURANT_ACCEPTED    { prepTimeMin }
//   RESTAURANT_REJECTED    { reason }
//   PAYMENT_CAPTURED       { txnId, amount }
//   PAYMENT_FAILED         { reason }
//   RIDER_ASSIGNED         { riderId }
//   RIDER_PICKED_UP        { riderId, at }
//   ORDER_DELIVERED        { at }
//   ORDER_CANCELLED        { reason, refundAmount? }
```

### 6.4 Idempotency

Every command carries an `Idempotency-Key` header. The Order Service stores it for 24h:

```
POST /api/v1/orders
Headers: Idempotency-Key: 7a8b9c...

First request  → creates order 12345, returns 202
Retry (network) → same key → returns cached 202 for order 12345 (no duplicate)
```

Without this, a customer double-tapping "Place Order" on a slow network creates two orders and two charges. Idempotency keys are non-negotiable for any payment-adjacent write.

---

<a id="7-request-flow"></a>
## 7. Step-by-Step Request Flow

### The Happy Path: Customer Places Order → Food Delivered

```
STEP 1: Customer taps "Place Order"
─────────────────────────────────
  App → POST /api/v1/orders (with Idempotency-Key)
  Gateway → validates JWT, rate-limits, routes to Order Service
  Order Service:
    a) INSERT INTO orders (status=PENDING_ACCEPT, saga_id=uuid)
    b) INSERT INTO order_events (ORDER_CREATED)    [same DB transaction]
    c) Outbox pattern: write event to order_events table + outbox table
    d) Commit transaction (now the order exists + event is durably stored)
    e) Return 202 { orderId, sagaId } to customer
    f) A separate "Outbox Publisher" process reads the outbox table and
       publishes ORDER_CREATED to Kafka (decouples DB commit from Kafka)

  WHY THE OUTBOX PATTERN?
    Naive approach: commit DB, then publish to Kafka.
    Problem: if commit succeeds but Kafka publish fails (network blip),
    the order exists in the DB but no other service knows → stuck order.
    Outbox: event is written in the SAME transaction as the order. A
    background publisher reliably ships it to Kafka. At-least-once delivery
    means consumers must be idempotent (they are — via event_id dedup).

STEP 2: Saga kicks off — Restaurant Acceptance
──────────────────────────────────────────────
  Kafka delivers ORDER_CREATED to:
    - Restaurant Service (consumer group "restaurant-service")
    - Notification Service (consumer group "notification-service")
    - Read Model Projector (consumer group "read-model")

  Restaurant Service:
    a) Consumes ORDER_CREATED
    b) Validates restaurant is open, items in stock
    c) Pushes order to restaurant's tablet via WebSocket (push notification)
    d) Restaurant operator taps Accept within 30s timeout
    e) On accept → publishes RESTAURANT_ACCEPTED { prepTimeMin: 25 }
    f) On timeout/reject → publishes RESTAURANT_REJECTED { reason }

STEP 3: Payment (only after restaurant accepts)
────────────────────────────────────────────────
  Order Service (saga orchestrator) consumes RESTAURANT_ACCEPTED:
    a) Updates order status → ACCEPTED
    b) gRPC call to Payment Service: Charge(customerId, amount, orderId)
    c) Payment Service calls gateway (Razorpay/Stripe)
    d) On success → Payment Service publishes PAYMENT_CAPTURED
    e) On failure → publishes PAYMENT_FAILED

  WHY gRPC HERE (not just events)?
    Payment is synchronous in user expectation — they want to know "did it
    work?" right now. The Order Service waits for the gRPC response. But
    the Payment Service ALSO publishes an event so other services (read
    model, notification) learn about it without a second call.

STEP 4: Rider Assignment (after payment captured)
─────────────────────────────────────────────────
  Order Service consumes PAYMENT_CAPTURED:
    a) Updates order status → PREPARING
    b) Publishes ASSIGN_RIDER command event (or makes gRPC to Rider Service)
    c) Rider Service:
       - GEORADIUS query on Redis for available riders within 2km
       - Sends push offer to top 3 riders
       - First to accept → publishes RIDER_ASSIGNED { riderId }
       - If all 3 reject/timeout → widen radius, retry with next batch

STEP 5: Real-time tracking begins
─────────────────────────────────
  Tracking Service consumes RIDER_ASSIGNED:
    a) Subscribes to that rider's GPS stream (rider app pings every 5s)
    b) Opens WebSocket channel to customer's app (via Gateway)
    c) Every rider ping → broadcast { lat, lng, status, eta } to customer

  Rider location flow:
    Rider App → POST /api/v1/rider/location (lat, lng)
    → Tracking Service → Redis (hot store) + Kafka tracking-events topic
    → ClickHouse (cold store for analytics + ETA model training)
    → WebSocket fanout to customer

STEP 6: Pickup & Delivery
─────────────────────────
  Restaurant marks order ready → Restaurant Service publishes ORDER_READY
  Rider picks up → Rider App publishes RIDER_PICKED_UP
  Order Service updates status → PICKED_UP, notifies customer
  Rider arrives → publishes ORDER_DELIVERED
  Order Service → status DELIVERED, closes saga

STEP 7: Final fanout
────────────────────
  ORDER_DELIVERED consumed by:
    - Notification Service → "Rate your order" push
    - Read Model Projector → updates Elasticsearch
    - Analytics → Kafka → data warehouse
    - Billing → settle restaurant payout, rider payout
```

### The Unhappy Path: Payment Fails After Restaurant Accepted

```
This is where the SAGA's compensating actions earn their keep.

  STEP 1: Order Created ✓
  STEP 2: Restaurant Accepted ✓ (kitchen may have even started prepping)
  STEP 3: Payment FAILED ✗

  Saga orchestrator (Order Service) detects PAYMENT_FAILED:
    a) Publishes ORDER_CANCELLED { reason: "PAYMENT_FAILED" }
    b) Publishes COMPENSATE_RESTAURANT_ACCEPT command
       → Restaurant Service consumes → releases inventory slot, 
         notifies kitchen to discard/hold the prep
    c) Order status → CANCELLED
    d) Notification → "Your order was cancelled, payment failed"
    e) Saga marked FAILED in saga table

  KEY INSIGHT: There is no distributed ROLLBACK. Each service undoes its
  own local transaction via a compensating event. The food might be wasted
  (real cost!), but the system state is consistent: no order exists, no
  payment, inventory released. Business metrics track "payment failure 
  waste" as a real KPI to minimize.
```

---

<a id="8-scaling-strategy"></a>
## 8. Scaling Strategy

### 8.1 Where the Bottlenecks Are

```
┌─────────────────────┬──────────────────┬─────────────────────────────┐
│ Component           │ Limiting factor  │ Mitigation                  │
├─────────────────────┼──────────────────┼─────────────────────────────┤
│ Order DB (Postgres) │ Writes/sec       │ Partition by customer_id    │
│ Menu reads          │ Read QPS         │ Elasticsearch + CDN cache   │
│ Rider location      │ 100K writes/s    │ Redis cluster, sharded GEO  │
│ WebSocket fanout    │ 500K connections │ Dedicated WS gateway fleet  │
│ Kafka partitions    │ Parallelism      │ 32+ partitions per topic    │
│ Payment gateway     │ External rate    │ Queue + backpressure        │
└─────────────────────┴──────────────────┴─────────────────────────────┘
```

### 8.2 Scaling the Write Path

```
Order DB sharding (when single PG instance saturates, ~5K writes/s):

  Shard by customer_id (hash):
    shard = customer_id % 16
    → 16 Postgres instances, each handling 1/16th of writes
  
  Why customer_id and not order_id?
    - "Show my orders" is the hot query → co-locate a customer's orders
    - Order_id sharding would scatter a customer's history across shards

  Cross-shard queries (rare, e.g., "restaurant's daily orders") → 
    use the read model (Elasticsearch) which is already aggregated.
```

### 8.3 Scaling the Read Path

```
Menu/Restaurant reads (55K/s):
  Layer 1: CDN (Cloudflare) — cache menu JSON for 60s. Hit rate ~90%.
           55K/s → 5.5K/s reaches backend after CDN.
  Layer 2: Redis cache (menu:restaurant:{id}) — TTL 5 min. Hit rate ~95%.
           5.5K/s → 275/s reaches Elasticsearch.
  Layer 3: Elasticsearch cluster (3 nodes, 32GB each)
  
  Effective: 55,000 reads/s served by ~275 Elasticsearch queries/s.
  This is why read-heavy apps survive — caching does 99% of the work.
```

### 8.4 Scaling Kafka

```
Throughput scales with partition count:
  - More partitions = more parallel consumers = higher throughput
  - But: more partitions = more replication overhead + longer rebalance

  Rule of thumb: max throughput per partition ≈ 10 MB/s.
  Our peak: 1.68 MB/s event throughput → 32 partitions is plenty.

Consumer scaling:
  - Each service runs N consumer instances
  - Kafka distributes partitions across instances in the consumer group
  - Can't have more consumers than partitions (extra consumers stay idle)
  - Monitor consumer lag → HPA scales pods up when lag grows
```

### 8.5 Scaling WebSocket Connections

```
The 500K concurrent tracking connections problem:
  
  One WebSocket gateway pod holds ~50K connections (memory-bound).
  Need: 500K / 50K = 10 pods minimum → run 15 for redundancy.

  Connection → order → rider mapping lives in Redis:
    ws:order:{orderId} → gatewayPodId

  When a rider location update arrives at Tracking Service:
    a) Lookup ws:order:{orderId} → which gateway pod holds the customer
    b) Publish update to that pod's internal channel (Redis Pub/Sub)
    c) Gateway pod pushes to the customer's WebSocket

  This is "sticky routing" — the rider update must reach the specific
  pod holding the customer's connection. Redis Pub/Sub is the fanout glue.
```

### 8.6 Database Connection Scaling

```
Each service pod opens DB connections. 100 pods × 10 connections = 1000.
PostgreSQL default max_connections = 100. Problem.

Solution: PgBouncer (connection pooler) in front of each DB.
  - 1000 client connections → 50 actual DB connections (multiplexed)
  - Transaction-mode pooling: connection checked out per transaction
  - Order DB throughput barely affected; connection storms averted
```

---

<a id="9-failure-modes"></a>
## 9. Failure Modes & Mitigation

### 9.1 Kafka Broker Failure

```
Scenario: One of 6 brokers crashes.

Impact: The 32 partitions it hosted (as leader) become briefly unavailable.
        Replicas on other brokers take over as new leaders (controller
        triggers re-election in ~seconds).

Mitigation:
  - Replication factor 3 → tolerate 2 broker failures per partition
  - min.insync.replicas = 2 → producer waits for 2 acks (durability > latency)
  - acks=all on producers → no acknowledged event is lost
  - Unclean leader election DISABLED → prefer consistency over availability
    (a partition with no in-sync replica stays unavailable rather than
    risk data loss from a stale replica taking over)

Customer impact: Brief latency spike (~5s) during leader election, no data loss.
```

### 9.2 Order Database Failure

```
Scenario: Primary Order DB goes down.

Mitigation:
  - Synchronous streaming replication to 1 sync replica (zero data loss)
  - Asynchronous replication to 1-2 more replicas (read scaling + DR)
  - Patroni (or RDS failover) promotes sync replica → new primary in <30s
  - Order Service retries writes with backoff during failover (200ms window)
  - Customer sees "placing order..." spinner for ~30s, then succeeds

  If primary AND sync replica both die (AZ failure):
    - Promote async replica (potential data loss of last few seconds)
    - Event log (Kafka, separate infra) still has everything → replay
      events to rebuild order state. This is why event sourcing is gold.
```

### 9.3 Payment Gateway Outage

```
Scenario: Razorpay/Stripe is down for 20 minutes.

The Payment Service must NOT block order placement. Pattern:

  Order Service → Payment Service (gRPC Charge)
    ↓ gateway timeout/failure
  Payment Service marks payment PENDING, publishes PAYMENT_PENDING
    ↓
  Order Service saga: order stays in ACCEPTED state, payment retried.
    - Retry with exponential backoff: 30s, 1m, 5m, 15m
    - If payment succeeds within 15m → saga continues normally
    - If still failing after 15m → publish ORDER_CANCELLED, 
      compensating action releases restaurant inventory

  Customer sees: "Payment processing, we'll confirm shortly"
  This is BETTER than failing the order outright — gateway outages are
  usually transient, and most payments succeed on retry.

Circuit breaker: after N consecutive failures, Payment Service stops
hammering the gateway (opens circuit), fails fast, and periodically
tests recovery (half-open). Prevents cascading latency.
```

### 9.4 Rider No-Show

```
Scenario: Rider assigned but never picks up (app crash, went offline, etc.)

Detection:
  - Rider assigned → ORDER_READY published by restaurant
  - Rider must confirm pickup within X minutes
  - Timeout → Tracking Service publishes RIDER_NO_SHOW

Mitigation:
  - Saga publishes COMPENSATE_RIDER_ASSIGNMENT (mark assignment EXPIRED)
  - Saga re-enters rider assignment step → finds new rider
  - Customer notified: "Reassigning your rider, slight delay"
  - Cap at 2 reassignments; after that, offer cancellation + refund

  KEY: The saga's state machine is RESUMABLE. It doesn't care that the
  first rider failed — it just re-enters the ASSIGN_RIDER state.
```

### 9.5 Poison Message (Consumer Crash Loop)

```
Scenario: A malformed event causes a consumer to throw, retry, throw...

Without protection: the consumer is stuck, lag grows, orders pile up.

Mitigation: Dead Letter Queue (DLQ)
  - Consumer catches exception, does NOT retry infinitely
  - After 3 retries → publish to <topic>-dlt (dead letter topic)
  - Original partition offset advances → consumer keeps processing
  - DLQ monitored by ops; bad events investigated + replayed after fix

  This is critical: one bad event must not block the entire pipeline.
```

### 9.6 Network Partition / Split Brain

```
Scenario: Order Service can reach Kafka but not Payment Service.

With synchronous gRPC to Payment: order placement fails (can't charge).
  → Customer sees error. Acceptable for payment (can't place unpaid orders).

With event-driven fanout (notification, read model): keeps working.
  → Decoupling pays off — non-critical services degraded, not dead.

PostgreSQL split-brain: Prevented by requiring quorum (Patroni + etcd).
  Only one primary is ever writable. A network-isolated former-primary
  demotes itself if it loses quorum.
```

### 9.7 Thundering Herd (Lunch Rush)

```
Scenario: At 12:00, traffic spikes 5× in 2 minutes.

Mitigation:
  - HPA scales pods based on Kafka consumer lag + CPU (pre-warmed at 11:45)
  - Predictive scaling: cron-scheduled scale-up before known peak windows
  - Rate limiting at gateway: if order QPS > capacity, return 429 with
    "Try again" (better than letting everything time out)
  - Graceful degradation: disable non-critical features (ratings, 
    recommendations) to shed load during spikes
```

---

<a id="10-trade-off-analysis"></a>
## 10. Trade-off Analysis

### 10.1 Eventual Consistency vs. Strong Consistency

```
Choice: Eventual consistency between services, strong within Order Service.

Cost:
  - Customer places order → "active orders" list updates 1-2s later
    (read model projector hasn't consumed the event yet)
  - Mitigated by returning the order in the POST response + client-side 
    optimistic update; the read model catches up.

Benefit:
  - Services scale independently (read model can be rebuilt without 
    touching the write path)
  - No distributed locks, no 2PC, no blocking waits across services

When to reconsider:
  - If regulators demand "payment captured" visible to customer within 
    100ms of order placement → can't rely on async read model. Would need
    synchronous read-after-write from the Order DB for that specific view.
```

### 10.2 Saga (Orchestration) vs. 2-Phase Commit

```
Choice: Orchestrated saga.

Cost:
  - More complex to reason about (state machine, compensations)
  - Temporary inconsistency window (restaurant accepted before payment 
    confirmed — food might be prepped and wasted if payment fails)
  - Developer must explicitly design compensating actions

Benefit:
  - No distributed locks → services stay available
  - No single coordinator that must hold locks across all services
  - Scales naturally — each saga step is just an event

Alternative — Choreography (no orchestrator):
  - Each service reacts to events autonomously, no central brain
  - Simpler to start, but harder to debug ("why did this order fail?")
  - Circular dependencies emerge (Payment waits for Restaurant, 
    Restaurant waits for Payment) → orchestration avoids this
```

### 10.3 Event Sourcing vs. CRUD

```
Choice: Event sourcing for order lifecycle.

Cost:
  - Complexity: must maintain event schema, versioning, migrations
  - Rebuilding state requires replaying all events (use snapshots for 
    long-lived aggregates — checkpoint every 100 events)
  - Developers must think in events, not "update the row"
  - Testing is harder (must test event sequences, not just final state)

Benefit:
  - Perfect audit trail (regulatory compliance, debugging, analytics)
  - Time travel: "what was the order state at 12:03:17?"
  - Read models are disposable (rebuild from events anytime)
  - Natural fit with Kafka (events ARE the integration contract)

When NOT to use event sourcing:
  - For the Restaurant menu (mostly static, CRUD is fine)
  - For rider profiles (CRUD)
  - Only the order LIFECYCLE justifies the complexity
```

### 10.4 CQRS — Separate Read Store

```
Choice: Elasticsearch + Redis as read models, separate from PostgreSQL writes.

Cost:
  - Two data stores to keep in sync (eventual consistency window)
  - More infrastructure to operate (ES cluster + Redis + PG)
  - Projection logic must be maintained (event → read model mapping)

Benefit:
  - Read QPS (55K/s) doesn't contend with write QPS (280/s)
  - Read model schema optimized for queries (denormalized, indexed)
  - Can add new read views without touching the write path

Alternative — single PostgreSQL with materialized views:
  - Simpler infra, but Postgres can't serve 55K reads/s as cheaply as ES
  - Materialized view refresh is expensive and blocks writes
```

### 10.5 gRPC vs. Pure Events for Saga Steps

```
Choice: Hybrid — gRPC for "need the answer" steps, events for fanout.

Cost:
  - Two communication mechanisms to maintain
  - gRPC couples caller to callee availability (Payment outage blocks 
    order placement — but that's desirable for payments)

Benefit:
  - Synchronous steps (payment) give immediate user feedback
  - Async fanout (notifications) decouples and scales

If we went pure-event for payment:
  - Order Service publishes CHARGE_REQUESTED
  - Payment Service consumes, charges, publishes PAYMENT_CAPTURED
  - More decoupled, but customer waits for the round-trip through Kafka
  - Acceptable, but gRPC's latency is lower for must-succeed-now steps
```

### 10.6 Monolith First vs. Microservices First

```
Honest assessment: For a startup, this architecture is OVERKILL.

If you're building food delivery v1 with 100 orders/day:
  - A single Rails/Django monolith with one PostgreSQL database
  - Background jobs (Sidekiq/Celery) for notifications
  - Is simpler, cheaper, faster to iterate on

The event-driven microservices architecture here is justified when:
  - Multiple teams own different domains (restaurant ops vs. rider ops 
    vs. payments — different release cadences, different on-call)
  - Scale demands it (50K orders/hour — a single DB struggles)
  - Fault isolation matters (payment outage shouldn't kill order placement)

The right migration path: monolith → modular monolith → extract 
microservices one at a time, starting with the one that hurts most 
(usually payments or rider assignment).
```

### 10.7 Summary — The Big Trade-offs at a Glance

```
┌──────────────────────┬──────────────────────────────┬──────────────────────────────┐
│ Decision             │ We Chose                     │ The Price We Pay             │
├──────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Communication        │ Kafka events + gRPC hybrid   │ Two integration styles       │
│ Consistency          │ Eventual + saga              │ Temp inconsistency window    │
│ Transactions         │ Saga with compensations      │ Compensating logic complexity│
│ Data model           │ Event sourcing + CQRS        │ Infra + projection upkeep    │
│ Deployment           │ Kubernetes microservices     │ Operational complexity       │
│ Read path            │ ES + Redis projections       │ Two stores to sync           │
│ Write path           │ PostgreSQL + outbox          │ Outbox publisher component   │
└──────────────────────┴──────────────────────────────┴──────────────────────────────┘

Every one of these trades simplicity for scalability and fault isolation.
For a platform doing millions of orders, that trade is worth it.
For a platform doing hundreds, it isn't. Pick based on YOUR scale.
```

---

## Appendix: Key Numbers to Remember

```
Throughput targets:
  - 50,000 orders/hour sustained, ~280/sec peak
  - 55,000 menu reads/sec peak (CDN + Redis absorb 99%)
  - 100,000 rider location updates/sec
  - 500,000 concurrent WebSocket connections

Latency budgets:
  - Order placement (write):  p99 < 500ms
  - Menu browse (read):       p99 < 100ms
  - Rider → customer screen:  < 2s end-to-end
  - Saga step completion:     < 30s (each step)

Storage:
  - ~27 KB per order (incl. events + tracking)
  - ~135 GB/day, ~49 TB/year
  - Kafka: 7-day hot + S3 cold archival

Replication & durability:
  - Kafka RF=3, min.insync.replicas=2, acks=all
  - PostgreSQL: 1 sync replica + 2 async replicas
  - No event is acknowledged until durably persisted on 2+ nodes
```

---

*This architecture is a blueprint, not a prescription. The patterns — saga, CQRS, event sourcing, outbox — are tools. Use the ones your problem actually needs. A team of 5 building for 1,000 orders/day should ship a monolith. A team of 50 building for 5M orders/day needs this.*

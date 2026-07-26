# Paytm — System Design Atlas

> **One-line summary:** Paytm is India's largest horizontal fintech super-app — a digital wallet, a
> UPI payments rail, a merchant QR-code payment network, a mini-app store, and a financial services
> marketplace — all built on a microservices backend that must be **strongly consistent for money**
> while surviving Diwali-scale traffic spikes of thousands of transactions per second.

---

## 1. Overview & Scale Numbers

Paytm ("Pay Through Mobile") started in 2010 as a prepaid mobile recharge site. The real inflection
point was **demonetisation (November 2016)**, when India withdrew 86% of its cash overnight and
digital payments exploded. The second inflection was **UPI (Unified Payments Interface)**, launched
by NPCI in 2016, which by 2024 was processing over **14 billion transactions a month** nationally.
Paytm rode both waves.

Today Paytm is not one product but a **super-app**: wallet, UPI, merchant payments, bill payments,
movies/travel/bus/flight bookings, mutual funds, insurance, gold, and a mini-app store where
third-party services run inside the Paytm container.

### The scale

| Metric                                            | Approximate value                  | Why it matters                                              |
| ------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------- |
| Registered users                                  | ~350M+                              | Largest fintech user base in India after banks               |
| Merchants on Paytm QR / Soundbox                  | ~35M+                               | The largest offline merchant network in India                |
| Monthly UPI transactions (through Paytm PPBL)     | billions (historically; post-2024 via partner banks) | UPI is the dominant rail; Paytm routes via banks       |
| Peak TPS (Diwali / IPL / sale days)               | tens of thousands of tx/sec          | Capacity must handle 5–10x average; SLA is money           |
| Payment success rate target                       | 99%+                                | Failed payments = lost trust + chargebacks                  |
| Vertical services (movies, travel, etc.)          | 100+                                | Each is its own domain with its own service team            |
| Mini-apps hosted                                  | thousands                           | Run inside Paytm's JS container, not separate installs       |
| Annual GTV (gross transaction value)              | ₹ trillions                         | Money flow size determines fraud-risk surface                |

### The product goal in one paragraph

A user opens Paytm, scans a merchant's QR code, and pays ₹150 from their wallet, UPI-linked bank
account, or stored card — in under 3 seconds — and the merchant's Paytm Soundbox instantly announces
"₹150 received." Behind the scenes, the money moves through one of several rails (Paytm Wallet
ledger, UPI, card network), the merchant's account is credited, a receipt is generated, and fraud
engines score the transaction in real time. Every rupee must be accounted for to the last paisa, with
zero double-spends, even when a million other people are doing the same thing at the same time.

---

## 2. High-Level Architecture

Paytm's architecture is best understood as **three planes stacked on top of each other**:

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                         SUPER-APP PLANE                              │
   │   Paytm App (Android/iOS/Web)  +  Mini-app container (JS sandbox)   │
   │   - Wallet UI, UPI UI, Recharge, Movies, Travel, Mutual Funds       │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │  HTTPS / TLS
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          EDGE / API GATEWAY                          │
   │       (TLS termination, auth, rate limit, WAF, request routing)      │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
   ┌──────────┐            ┌──────────────┐           ┌──────────────┐
   │ User &   │            │  Payments    │           │  Merchant    │
   │ Account  │            │  Orchestration│          │  Platform    │
   │ Service  │            │  (core)      │           │  (QR/SB/MIS) │
   └──────────┘            └──────┬───────┘           └──────────────┘
                                   │
            ┌──────────────────────┼─────────────────────┐
            ▼                      ▼                     ▼
   ┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐
   │  Ledger /      │    │  Rail Adapter    │   │  Fraud & Risk    │
   │  Wallet Svc    │    │  Layer           │   │  Engine          │
   │  (double-entry)│    │  (UPI/Card/NB)   │   │  (real-time ML)  │
   └────────────────┘    └────────┬─────────┘   └──────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          BANKING / NPCI RAILS                         │
   │   NPCI (UPI switch)  ·  RuPay  ·  Visa/Mastercard  ·  Partner Banks  │
   └──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          EVENT / DATA PLANE                           │
   │   Kafka (txn events) → Analytics, Notifications, ML features, Audit  │
   └──────────────────────────────────────────────────────────────────────┘
```

**The three planes:**

1. **Super-app plane** — the app itself + the mini-app runtime that hosts third-party services.
2. **Payments plane** — where money actually moves. This is the part that must be strongly
   consistent and never lose a rupee.
3. **Data plane** — everything asynchronous: notifications, analytics, fraud feature aggregation,
   compliance reporting.

### The key abstraction: the Transaction State Machine

Every payment is a state machine. The orchestrator drives it through transitions, each of which is
either durable (persisted before the caller proceeds) or async (emitted as an event).

```
   [INITIATED] ──risk check pass──▶ [PROCESSING] ──rail returns SUCCESS──▶ [SUCCESS]
        │                                │
        │                                └──rail returns FAIL──▶ [FAILED] → refund flow
        │
        └──risk check FAIL──▶ [BLOCKED] → user notified, manual review queue
```

The single most important rule: **transitions to SUCCESS are idempotent and conditional.** Only one
thread can flip a transaction to SUCCESS, and a retried API call must never double-charge.

---

## 3. Detailed Component Breakdown

### 3.1 User & Account service

Classic CRUD over a sharded MySQL/PostgreSQL cluster. Owns user profiles, KYC status (Aadhaar/PAN
verification — mandatory for wallet load above ₹10,000/month and for UPI), linked bank accounts,
stored payment instruments (cards tokenised per RBI rules), and device/app settings. KYC is a slow,
external dependency (calls to UIDAI for Aadhaar e-KYC), so it runs on a separate queue and never
blocks the payment path.

### 3.2 Merchant platform (QR + Soundbox + MIS)

The merchant side of Paytm is its own large subsystem. Three pieces:

- **QR onboarding & management** — merchants get a static QR linked to their account; the QR encodes
  a merchant ID (and for UPI QR, a VPA like `merchant@paytm`). Each QR maps to a merchant record
  that owns settlement details (which bank account to credit).
- **Soundbox** — the small voice device that announces "₹150 received." It's a low-cost IoT device
  that holds a persistent connection (MQTT or long-poll) to Paytm's notification service and plays a
  pre-rendered or TTS audio clip when a payment lands.
- **Merchant MIS / dashboard** — daily settlement reports, sale summaries, item-level catalog, loans
  eligibility. Backed by a read replica or an analytics warehouse (ClickHouse / Hive).

### 3.3 Payment Orchestration service (the conductor)

This is the brain of the payment. It receives a pay request, picks the rail, calls the fraud engine,
drives the rail adapter, writes the ledger, and emits the success/failure event. Think of it as a
state machine executor: it knows the current state of the transaction and the legal next transitions.

```
   pay_request arrives
        │
        ▼
   ┌──────────────────────────────────────────────────┐
   │  1. Idempotency check (have I seen this key?)    │
   │  2. Risk score (fraud engine, blocking)          │
   │  3. Pick rail (UPI > Wallet > Card)              │
   │  4. Call rail adapter                            │
   │  5. On success: write ledger (double-entry)      │
   │  6. Emit TXN_SUCCESS event to Kafka              │
   │  7. Return to client                             │
   └──────────────────────────────────────────────────┘
```

The orchestrator is **stateless**. All state lives in the transaction store and the ledger. This
makes it horizontally scalable — you can spin up more instances during Diwali and they'll all behave
identically.

### 3.4 Ledger / Wallet service (double-entry bookkeeping)

Paytm Wallet is a **closed-system prepaid payment instrument (PPI)** regulated by RBI. Internally it
is a double-entry ledger: every rupee that enters the wallet must have a matching entry on the other
side. If you load ₹500 from your card, the ledger records:

```
   Debit  Card Settlement Account   ₹500
   Credit User Wallet Balance       ₹500
```

When you pay a merchant ₹150:

```
   Debit  User Wallet Balance       ₹150
   Credit Merchant Payable Account  ₹150
```

The sum of all debits and credits across all accounts is **always zero**. This invariant is enforced
inside a single database transaction per journal entry — if any leg fails, the whole entry rolls
back. This is the only way to guarantee no money is created or destroyed.

### 3.5 Rail adapter layer

Abstracts the external payment rails. Each adapter speaks the rail's protocol and normalises the
response into Paytm's internal result codes.

```
   ┌─────────────────────────────────────────────────────────┐
   │                  Rail Adapter Layer                     │
   │                                                         │
   │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐ │
   │   │  UPI    │  │  Card   │  │  Net    │  │  Wallet  │ │
   │   │ Adapter │  │ Adapter │  │ Banking │  │  (internal│ │
   │   │(NPCI)   │  │(Visa/MC)│  │ Adapter │  │   ledger)│ │
   │   └─────────┘  └─────────┘  └─────────┘  └──────────┘ │
   └─────────────────────────────────────────────────────────┘
```

The UPI adapter talks to NPCI's UPI switch over the **UPI API** (secure VPN, ISO 20022-style XML or
JSON payloads). The card adapter talks to acquiring banks → card networks (Visa/Mastercard/RuPay).
Net banking adapter talks to each bank's gateway.

**Timeouts are critical here.** UPI has a strict timeout (~30s). If Paytm doesn't get a response, it
cannot assume failure (the money may have moved) — it must query the rail for status before marking
the transaction. This is the source of most "money debited but payment failed" edge cases, and
Paytm's reconciliation engine handles it.

### 3.6 Fraud & Risk engine

Scores every transaction in **<50ms**. Uses a combination of:

- **Rules** — velocity checks ("user did 50 txns in last minute"), blocklists, geo-velocity ("card
  used in Delhi and Mumbai in 5 minutes").
- **ML model** — a gradient-boosted model (XGBoost or similar) trained on historical fraud labels,
  fed by real-time feature aggregates from Kafka.

High-risk transactions are blocked or sent to manual review. The risk decision is **synchronous** in
the payment path — if the risk engine is slow, the whole payment is slow, so it is heavily cached and
horizontally scaled.

### 3.7 Mini-app / super-app container

Third-party services (e.g., a food-delivery brand) run inside Paytm as JS mini-apps. The container
provides a sandboxed JS runtime, native bridge APIs (camera, location, payments), and a payment
SDK — so a mini-app can invoke Paytm's payment flow without leaving the container. Architecturally
this is similar to WeChat mini-programs: a JS VM, a native bridge, and a CDN for mini-app bundles.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │    User      │     │   Merchant   │     │   Instrument     │
   │ - id         │     │ - id         │     │ - id             │
   │ - phone      │     │ - name       │     │ - user_id        │
   │ - kyc_status │     │ - vpa        │     │ - type (CARD/UPI)│
   │ - wallet_id  │     │ - settlement │     │ - token_ref      │
   └──────────────┘     │   _bank_acct │     │ - (masked pan)   │
                        └──────────────┘     └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │                      TRANSACTION                             │
   │ - id (uuid)                                                  │
   │ - user_id, merchant_id (nullable for P2P)                    │
   │ - amount (paisa, integer), currency                          │
   │ - rail (UPI/WALLET/CARD/...)                                 │
   │ - status (INITIATED/PROCESSING/SUCCESS/FAILED/REFUNDED)      │
   │ - idempotency_key (unique)                                   │
   │ - rrn (bank reference number, from rail)                     │
   │ - created_at, updated_at                                     │
   └────────────────────┬─────────────────────────────────────────┘
                        │ 1
                        ▼ *
               ┌──────────────────┐
               │  Transaction     │   (event-sourced log; one row
               │  Event           │    per state transition)
               │ - txn_id         │
               │ - event_type     │
               │ - payload (JSON) │
               │ - ts             │
               └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │                       LEDGER ENTRY                          │
   │ - id (sequential)                                            │
   │ - txn_id                                                     │
   │ - account_id (wallet/bank/settlement)                        │
   │ - debit / credit (always paired; sum = 0 per txn_id)        │
   │ - amount, currency                                           │
   │ - ts                                                         │
   └──────────────────────────────────────────────────────────────┘
```

### 4.2 Storage choices

| Data                                  | Store                          | Why                                                    |
| ------------------------------------- | ------------------------------ | ------------------------------------------------------ |
| User & Merchant profiles              | MySQL/PostgreSQL (sharded)     | Strong consistency, transactional, mature ops          |
| Transactions (live)                   | MySQL/PostgreSQL (sharded by user_id) | Index by idempotency_key, status, user_id         |
| Wallet ledger (double-entry)          | MySQL with row-level locking   | ACID is non-negotiable for money                       |
| Transaction events (history)          | Kafka → S3 / Hive warehouse    | Append-only; analytics and audit                       |
| QR → merchant mapping                 | Redis (hot) + MySQL (source)   | Every scan does a lookup; must be <5ms                 |
| Soundbox connection state             | Redis / MQTT broker            | Millions of always-on devices                          |
| Fraud feature aggregates (velocity)   | Redis (counters, sorted sets)  | Sub-millisecond read/write in the risk path            |
| Mini-app bundles                      | CDN + S3                       | Static assets, read-heavy                              |
| Settlement / MIS reports              | ClickHouse / Hive              | Columnar, fast aggregation over billions of rows       |

### 4.3 Why money lives in a relational DB

You cannot use an eventually-consistent store for balances. If two nodes disagree about your wallet
balance for even a second, you can double-spend. The ledger is therefore a **sharded MySQL cluster
with serializable transactions per account**. Sharding key: `user_id` (a user's wallet, transactions,
and ledger entries all live on the same shard, so intra-user operations are single-node ACID).

### 4.4 Idempotency everywhere

Every payment API call carries an `idempotency_key` (usually a UUID generated client-side). The
transaction table has a unique index on `(user_id, idempotency_key)`. A retried call hits the same
row and returns the existing result instead of creating a new transaction. This single pattern
prevents 99% of double-charge bugs.

---

## 5. Request Flow — Paying a Merchant via Paytm QR

```
USER APP    API GW    ORCHESTRATOR   RISK     LEDGER    RAIL(UPI)    MERCHANT SVC    KAFKA
   │           │            │          │         │          │             │           │
   │─scan QR──▶│            │          │         │          │             │           │
   │           │            │          │         │          │             │           │
   │─pay ₹150─▶│            │          │         │          │             │           │
   │  (amount, │            │          │         │          │             │           │
   │   merchID,│            │          │         │          │             │           │
   │   idemp_  │            │          │         │          │             │           │
   │   key,    │            │          │         │          │             │           │
   │   rail:UPI)            │          │         │          │             │           │
   │           │─route────▶│          │         │          │             │           │
   │           │            │          │         │          │             │           │
   │           │            │─check idemp key (DB) ─── not seen, create txn INITIATED   │
   │           │            │          │         │          │             │           │
   │           │            │─score────▶│        │          │             │           │
   │           │            │◀──OK──────┤        │          │             │           │
   │           │            │          │         │          │             │           │
   │           │            │  txn → PROCESSING                                        │
   │           │            │          │         │          │             │           │
   │           │            │─debit user / credit merchant (pending)──▶│              │
   │           │            │                            (rail call)    │              │
   │           │            │──────────────UPI collect req (VPA, amt)─▶│              │
   │           │            │                                              │             │
   │           │            │                    (NPCI → banks → user bank approves)   │
   │           │            │◀──────────────UPI success + RRN──────────┤              │
   │           │            │          │         │          │             │           │
   │           │            │  ledger: finalize debit/credit entries (commit txn)      │
   │           │            │          │         │          │             │           │
   │           │            │  txn → SUCCESS                                          │
   │           │            │                                          │             │
   │           │            │─emit TXN_SUCCESS ──────────────────────────────────────▶│
   │           │            │                                                         │
   │           │            │                                          │             │
   │           │            │                                          │─credit merchant│
   │           │            │                                          │   settlement │
   │           │            │                                          │             │
   │◀──payment success + receipt────────┤              │             │             │
   │           │            │                                                         │
   │                                                       (merchant soundbox beeps)   │
```

**Step-by-step:**

1. **User scans QR.** App decodes the QR (VPA `merchant@paytm` or a static Paytm QR ID), shows
   merchant name and a payment field.
2. **User enters ₹150, taps Pay.** App calls `/pay` with amount, merchant ID, idempotency key,
   chosen rail (UPI), and user credentials (UPI PIN handled securely by the bank/NPCI flow).
3. **Orchestrator checks idempotency.** Looks up `(user_id, idempotency_key)` in the txn table. If
   found, returns the existing result. Otherwise inserts a new row with status `INITIATED`.
4. **Risk engine scores** the transaction synchronously. Rules + ML model. If blocked → status
   `BLOCKED`, user notified, no rail call.
5. **Txn → `PROCESSING`.** Orchestrator prepares the ledger entries (pending) and calls the UPI rail
   adapter.
6. **UPI adapter calls NPCI.** A UPI "collect" or "pay" request flows to NPCI → remitter bank →
   beneficiary bank. The user's bank debits their account. NPCI returns a success with an RRN
   (Retrieval Reference Number).
7. **On success: ledger commits.** Debit user wallet/bank, credit merchant payable account — both
   inside one ACID transaction. Txn → `SUCCESS`.
8. **Emit `TXN_SUCCESS` to Kafka.** Downstream consumers react asynchronously: notification service
   sends SMS/push to user; merchant service credits the merchant's settlement account; Soundbox
   service pushes an audio clip to the merchant's device; analytics ingests the event.
9. **User sees success screen + receipt.** Merchant's Soundbox announces "₹150 received."

### Failure & timeout handling

If the rail times out, the orchestrator **must not assume failure.** It marks the txn as
`PROCESSING`, then a **reconciliation job** queries the rail's status API. Based on the rail's
authoritative answer, the txn is moved to `SUCCESS` (with a late ledger commit) or `FAILED` (with a
refund if any debit happened). This reconciliation loop runs until the txn reaches a terminal state.

---

## 6. Scaling Strategy

### 6.1 Shard by user_id

Transactions, wallet balances, and ledger entries are all sharded by `user_id`. A user's entire
financial state lives on one shard, so intra-user operations (debit this user, credit that user)
that span two users use **two-phase commit or saga** patterns to keep both shards consistent.

### 6.2 Read replicas for the read-heavy paths

Profile reads, transaction history, merchant lookups — all served from read replicas. Writes go to
the primary. The app tolerates sub-second replication lag for most reads; money-critical reads
(balance before debit) always hit the primary.

### 6.3 Redis as the hot cache

- QR → merchant mapping: tens of millions of entries, read on every scan. Redis cluster, replicated.
- Risk velocity counters: `INCR` per user/merchant/card per minute window.
- Session tokens, OTP attempt counters.

### 6.4 Kafka as the backbone

Every payment emits events. Consumers are decoupled — notifications, fraud ML feature stores,
analytics, compliance, the data warehouse. Adding a new consumer (e.g., a new loyalty service)
doesn't touch the payment path. During Diwali, Kafka absorbs the spike; consumers lag but the payment
itself stays fast.

### 6.5 Autoscaling the orchestrator

The orchestrator is stateless. HPA (horizontal pod autoscaler) watches CPU + custom metric
(tps-per-pod) and spins up more instances during sale hours. Capacity is pre-provisioned for known
spikes (Diwali midnight, IPL final, Flipkart Big Billion Days).

### 6.6 Soundbox push at scale

~35M merchant devices, each holding a persistent connection. This is MQTT-broker scale — a fleet of
connection servers (think: a chat server farm) with consistent hashing of device_id → broker. When a
payment lands, the notification service publishes to the broker, which pushes to the device.

### 6.7 Multi-region active-active

Paytm runs multiple regions in AWS India (or equivalent). Payment state is pinned to the user's home
region (data residency under RBI rules). Cross-region traffic is minimised; each region is
self-sufficient for its shard of users.

---

## 7. Tech Stack

| Layer                          | Technology                                                              |
| ------------------------------ | ----------------------------------------------------------------------- |
| Mobile apps                    | Kotlin (Android), Swift (iOS), React Native (parts), JS mini-app runtime |
| Backend languages              | Java (Spring Boot), Go, Python (ML / data), Node.js (some services)     |
| API gateway                    | NGINX + custom layer; Kubernetes ingress                                  |
| Databases                      | MySQL (sharded), PostgreSQL, Cassandra (some hot KV)                     |
| In-memory / cache              | Redis cluster                                                           |
| Messaging                      | Apache Kafka                                                            |
| Search                         | Elasticsearch (merchant search, support search)                          |
| Analytics warehouse            | Hive / Spark on S3, ClickHouse for real-time dashboards                 |
| ML platform                    | Python, XGBoost / LightGBM, TensorFlow; feature store (Feast / custom)  |
| Mini-app runtime               | Custom JS VM sandbox with native bridge                                  |
| Soundbox / IoT                 | MQTT broker, custom firmware                                            |
| Observability                  | Prometheus + Grafana, ELK, distributed tracing (Jaeger/OpenTelemetry)   |
| Deployment                     | Kubernetes, Docker, Spinnaker / Argo CD                                  |
| Cloud                          | AWS (primary), with multi-AZ and DR                                      |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐                ┌──────────────────────┐              ┌──────────────┐
   │  User App  │◀──REST + WS────▶│  Node.js / Python    │◀──REST──────▶│ Merchant Web │
   │  (browser) │                │   backend            │              │   (browser)  │
   └────────────┘                │  - Express / FastAPI │              └──────────────┘
                                  │  - PostgreSQL       │
                                  │  - Redis            │
                                  │  - Kafka (optional) │
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │   Mock UPI / Bank   │   ← simulate a rail
                                  │   adapter (sandbox) │
                                  └─────────────────────┘
```

### 8.2 The ledger in 40 lines

The core: a double-entry ledger enforced by a Postgres transaction.

```python
def transfer(conn, txn_id, from_acct, to_acct, amount_paisa):
    with conn:  # transaction
        with conn.cursor() as cur:
            # lock both accounts in deterministic order to avoid deadlocks
            accts = sorted([from_acct, to_acct])
            cur.execute("SELECT balance FROM accounts WHERE id IN %s FOR UPDATE",
                        (tuple(accts),))
            # debit / credit
            cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s",
                        (amount_paisa, from_acct))
            cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s",
                        (amount_paisa, to_acct))
            # ledger entries
            cur.execute("""INSERT INTO ledger (txn_id, account_id, debit, credit)
                           VALUES (%s,%s,%s,0), (%s,%s,0,%s)""",
                        (txn_id, from_acct, amount_paisa,
                         txn_id, to_acct, amount_paisa))
```

`SELECT ... FOR UPDATE` locks the rows so concurrent transfers can't double-spend.

### 8.3 Idempotency

```python
def pay(user_id, merchant_id, amount, idempotency_key):
    existing = db.execute(
        "SELECT status, result FROM transactions WHERE user_id=%s AND idempotency_key=%s",
        (user_id, idempotency_key))
    if existing:
        return existing  # retried call, return cached result
    # ... create txn, call rail, ...
```

Unique index on `(user_id, idempotency_key)` makes this bulletproof even under races.

### 8.4 Mock the UPI rail

Build a fake `/upi/collect` endpoint that returns success 95% of the time and a random failure 5%.
This lets you practise the timeout + reconciliation flow — the hardest part of real payments.

### 8.5 Soundbox simulator

A small browser page that opens a WebSocket to your backend and `Audio.play()`s a TTS clip on
`TXN_SUCCESS` events. That's 90% of the Soundbox.

### 8.6 What you'll learn

- How double-entry bookkeeping makes money systems auditable.
- Why idempotency keys are the single most important pattern in payments.
- How to handle the "I don't know if it succeeded" timeout problem.
- Why read-heavy and write-heavy paths need different storage choices.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                       | Alternative considered       | Why Paytm chose it                                              |
| ---------------------------------------------- | ---------------------------- | --------------------------------------------------------------- |
| **Double-entry ledger for wallet**             | Single balance column        | Auditability; impossible to "lose" money; regulator-friendly   |
| **Shard by user_id**                           | Shard by txn_id              | All user state co-located; intra-user ops are single-shard ACID |
| **Stateless orchestrator**                     | Stateful per-txn worker      | Horizontal autoscaling during Diwali                            |
| **Synchronous risk check in payment path**     | Async post-hoc review only   | Block fraud before money leaves; cost is added latency          |
| **Event-sourced txn history (Kafka)**          | Only mutable txn rows        | Replay, audit, decoupled consumers (notifications, analytics)  |
| **QR encodes merchant VPA / ID, not amount**   | Static amount QR             | One QR per merchant; amount comes from user input              |
| **Idempotency key on every write**             | Trust client dedup           | Mobile networks are flaky; double-charge = trust catastrophe    |
| **Soundbox over MQTT, not polling**            | HTTP polling                 | Battery + bandwidth; ~35M devices polling would melt networks   |

### The deepest trade-off

**Latency vs. correctness in the payment path.** Every millisecond of latency in the orchestrator
shows up as a worse conversion rate (users abandon slow payments). But cutting corners on
correctness — skipping a ledger lock, returning success before the rail confirms — means lost money
and regulator action. Paytm (and every payment system) errs toward correctness: the ledger commit
and rail confirmation are synchronous, even at the cost of 200–500ms of added latency.

---

## 10. Common Interview Questions

**Q1: Design Paytm / a digital wallet.**
Start with the two sides: users and merchants. Walk through the payment flow. Emphasise the ledger
(double-entry), idempotency, and the rail abstraction. Discuss the timeout problem. Mention the
super-app aspect briefly.

**Q2: How do you prevent double-charging?**
Idempotency keys on every API call, unique-indexed in the txn table. Retries hit the same row. The
rail adapter also uses the rail's own idempotency mechanism (UPI RRN).

**Q3: How do you handle a payment that timed out?**
Never assume failure. Mark txn `PROCESSING`, run a reconciliation job that queries the rail's status
API, and move the txn to its true terminal state. Refund if money moved but txn failed.

**Q4: Why a double-entry ledger?**
Sum of debits and credits is always zero; any bug shows up as an imbalance. Regulators love it.
Audit is trivial. Single-balance columns can drift silently.

**Q5: How do you scale to Diwali-level TPS?**
Shard by user_id. Stateless orchestrator autoscales. Redis for hot lookups (QR, risk counters).
Kafka absorbs async load. Pre-provision capacity for known spikes.

**Q6: How does the Soundbox know a payment arrived?**
The merchant device holds a persistent connection (MQTT/long-poll) to a connection server. On
`TXN_SUCCESS`, the notification service publishes to the broker; the device plays the TTS clip.

**Q7: Wallet vs. UPI — what's the difference architecturally?**
Wallet is an internal closed-loop PPI: ledger entries are local, instant, and free. UPI is an
external rail: Paytm calls NPCI, which orchestrates inter-bank money movement. Wallet is faster and
cheaper but regulated as a PPI (with limits). UPI is open and dominant.

**Q8: How would you design the fraud engine?**
Synchronous in the payment path. Rules for velocity / blocklist / geo. ML model (XGBoost) fed by
real-time feature aggregates from Kafka. Latency budget <50ms. High-risk txns blocked or queued for
manual review.

**Q9: How do you ensure exactly-once semantics?**
Idempotency keys at every layer (app, API, DB, rail). Ledger commits are ACID. Downstream consumers
are idempotent (re-processing an event doesn't double-credit a loyalty point, because of unique
constraints).

**Q10: What if a shard goes down?**
 synchronous replication to a hot standby within the shard's region. Failover promotes the standby.
Backups to S3 for point-in-time recovery. Money-critical state is never single-copy.

---

## Further reading

- Paytm Engineering Blog (engineering.paytm.com) — merchant platform, Soundbox, scaling posts.
- NPCI UPI documentation — the protocol Paytm talks for UPI.
- RBI Master Directions on PPIs — the regulatory constraints on wallet design.
- Martin Kleppmann, *Designing Data-Intensive Applications*, ch. 7 (transactions) — for the ledger
  consistency reasoning.
- Pat Helland on event sourcing and exactly-once — for the reconciliation mindset.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

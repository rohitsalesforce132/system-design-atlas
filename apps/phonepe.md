# PhonePe — System Design Atlas

> **One-line summary:** PhonePe is India's largest UPI payments super-app — a thin client over the
> NPCI-owned UPI rail — combined with a mini-app store, an insurance/mutual-fund marketplace, and the
> **Indus Appstore**, all built on a microservices backend whose central job is to orchestrate
> billions of UPI transactions per month with sub-3-second latency while never double-charging anyone.

---

## 1. Overview & Scale Numbers

PhonePe launched in **2015** as one of the first UPI-based payment apps (teaming with Yes Bank as
its banking partner). Unlike Paytm, which started as a wallet and bolted on UPI, PhonePe was **UPI-
native from day one** — there is no "Wallet vs. UPI" fork; UPI is the spine.

PhonePe crossed **100M users in 2017**, hit the **#1 UPI app spot by transaction count** around 2020,
and by 2024 was handling close to **50% of all UPI transaction volume in India** — a staggering
share of a rail that itself does 14+ billion transactions a month nationally.

### The scale

| Metric                                            | Approximate value                | Why it matters                                              |
| ------------------------------------------------- | --------------------------------- | ----------------------------------------------------------- |
| Registered users                                  | ~500M+                            | Largest UPI app user base                                   |
| Monthly active users                              | ~250M+                            | Engagement scale                                            |
| UPI market share (by tx count)                    | ~45–50%                           | Nearly half of all UPI traffic flows through PhonePe        |
| Monthly UPI transactions                          | 5–6+ billion                      | Multi-billion monthly scale                                  |
| Merchants (offline, QR)                           | ~35M+                             | Largest offline UPI acceptance network                      |
| Peak TPS (Diwali, IPL, payday)                    | tens of thousands of tx/sec       | Capacity for 10x average bursts                             |
| Target end-to-end latency                         | <3 seconds                        | User-visible; includes UPI round-trip                       |
| Payment success rate target                       | 99%+                              | Failed UPI = lost trust                                     |
| Mini-apps / Indus Appstore apps                   | thousands                         | PhonePe is a super-app, not just payments                   |

### The product goal in one paragraph

A user opens PhonePe, taps "To Bank Account" or scans a merchant's UPI QR, enters a UPI PIN, and the
money moves from their bank account to the recipient's bank account — usually in under 3 seconds —
routed entirely through the NPCI UPI rail. PhonePe doesn't hold the money (no wallet in the default
flow); it acts as a sophisticated **orchestration layer** over UPI. Behind the scenes, it talks to
NPCI, handles failures and timeouts, scores fraud, renders the result, and notifies both sender and
receiver. The same app also lets users recharge, pay bills, buy insurance, invest in mutual funds,
book travel, and run thousands of mini-apps — but UPI is the spine that everything else hangs off.

---

## 2. High-Level Architecture

PhonePe's architecture is best understood as **an orchestration layer on top of an external rail**.
The rail (UPI, owned by NPCI) is a black box that PhonePe cannot control — it can only call it,
handle its responses, and hide its failures from the user.

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                       SUPER-APP PLANE                                │
   │   PhonePe App (Android/iOS) + Mini-app JS runtime + Indus Appstore  │
   │   - UPI UI, Recharge, Bills, Insurance, Mutual Funds, Travel        │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │  HTTPS / TLS, mTLS to backend
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                         EDGE / API GATEWAY                           │
   │       (TLS, auth, device attestation, rate limit, WAF, routing)     │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
   ┌──────────┐            ┌──────────────┐           ┌──────────────┐
   │  User &  │            │   Payment    │           │   Merchant   │
   │  VPA Svc │            │ Orchestration│           │   Platform   │
   │  (KYC,   │            │   (core)     │           │  (QR, MIS)   │
   │   device)│            │              │           │              │
   └──────────┘            └──────┬───────┘           └──────────────┘
                                   │
            ┌──────────────────────┼─────────────────────┐
            ▼                      ▼                     ▼
   ┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐
   │  UPI Adapter   │    │  Fraud & Risk    │   │  Switch /        │
   │  (talks to     │    │  Engine          │   │  Idempotency     │
   │   NPCI)        │    │  (real-time ML)  │   │  Service         │
   └────────┬───────┘    └──────────────────┘   └──────────────────┘
            │
            │  secure VPN, signed payloads
            ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                            NPCI UPI SWITCH                            │
   │   NPCI routes between PSP banks → remitter bank → beneficiary bank   │
   └──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          EVENT / DATA PLANE                           │
   │   Kafka (txn events) → Notifications, Analytics, ML, Compliance      │
   └──────────────────────────────────────────────────────────────────────┘
```

### The single most important constraint

**PhonePe does not custody the money.** In the default UPI flow, money moves directly from sender's
bank to receiver's bank via NPCI. PhonePe is a **PSP (Payer/Beneficiary PSP)** — it formats UPI
requests, signs them, sends them to NPCI, and parses responses. This is fundamentally different from
a wallet: there is no internal ledger to balance, but there is also no local source of truth — the
**UPI rail itself is the source of truth**, and PhonePe must reconcile against it constantly.

### The key abstraction: the Transaction State Machine (mirroring UPI)

```
   [INITIATED] ──risk pass──▶ [RAILED] ──NPCI ACK──▶ [PROCESSING]
                                                       │
                                       ┌───────────────┴───────────────┐
                                       ▼                               ▼
                              NPCI SUCCESS                      NPCI FAILURE / TIMEOUT
                                  │                                │
                                  ▼                                ▼
                              [SUCCESS]              [PENDING] ──recon job──▶ [SUCCESS/FAILED]
```

The hardest state is **`PENDING`** — PhonePe sent the request, NPCI hasn't answered, and the money
may or may not have moved. This is where reconciliation earns its keep.

---

## 3. Detailed Component Breakdown

### 3.1 User & VPA service

Owns user accounts, device registration (UPI requires the device to be "registered" with the PSP
bank — a security binding), and **VPA (Virtual Payment Address)** management. A VPA looks like
`rohit@ybl` (ybl = Yes Bank Ltd, PhonePe's original partner prefix) or `rohit@ibh` (ICICI). Each VPA
maps to a real bank account at the partner bank.

VPA → bank account mapping lives at the **partner bank**, not PhonePe. PhonePe holds a cached copy
but defers to the bank for truth. This is a recurring pattern: PhonePe is an orchestration layer
over bank-owned state.

### 3.2 Payment Orchestration service

The brain. Receives a pay request, picks the flow (P2P to VPA, P2M to merchant QR, collect request,
etc.), calls the risk engine, drives the UPI adapter, persists the transaction, and emits events.
Stateless and horizontally scalable.

```
   pay_request arrives
        │
        ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  1. Idempotency check (idempotency_key lookup)                │
   │  2. Risk score (synchronous)                                  │
   │  3. Resolve VPA → bank account (cache → bank API)             │
   │  4. Build UPI payload + sign with PSP credentials             │
   │  5. Call UPI adapter → NPCI                                   │
   │  6. Persist result + RRN                                      │
   │  7. Emit TXN_SUCCESS / TXN_FAILED event                       │
   │  8. Return to client                                          │
   └───────────────────────────────────────────────────────────────┘
```

### 3.3 UPI adapter (the rail talker)

Speaks NPCI's UPI protocol over a **secure private network (VPN)** with mTLS and payload signing.
Normalises NPCI's response codes into PhonePe's internal result codes. Critically, it must:

- Honour UPI's strict timeouts (~30s end-to-end).
- Implement **idempotent status queries** — call NPCI's `txnStatus` API when a request times out.
- Retry only safe, idempotent operations (never re-send a payment that might have succeeded).

```
   ┌─────────────────────────────────────────────────────┐
   │                UPI Adapter                           │
   │                                                     │
   │   payV1(payload, signature)    ──▶ NPCI             │
   │   collectV1(...)               ──▶ NPCI             │
   │   txnStatus(rrn)               ──▶ NPCI  ◀── recon  │
   │   listVPA(user)                ──▶ partner bank     │
   └─────────────────────────────────────────────────────┘
```

### 3.4 Switch / Idempotency service

A thin service (often backed by Redis + MySQL) that ensures every `(user_id, idempotency_key)` pair
maps to exactly one transaction. Without this, a flaky mobile network retrying a payment could create
two transactions. It's the **first gate** the orchestrator checks.

### 3.5 Fraud & Risk engine

Scores every transaction in real time. Because PhonePe doesn't hold balances, fraud signals are
different from a wallet's:

- **Velocity** — same VPA doing 50 txns/minute.
- **New device + high value** — device fingerprinting.
- **Benami / mule account patterns** — ML model on VPA graph behaviour.
- **Merchant velocity anomalies** — a sudden spike at a merchant QR.

Decisions: allow, block, or step-up (require re-entering UPI PIN or an OTP). Synchronous in the path;
latency budget ~50ms.

### 3.6 Merchant platform

Merchant onboarding (KYC, settlement bank account), QR issuance (static UPI QR encoded with the
merchant VPA), merchant MIS / dashboard, settlement service. Settlement is **T+1 by default** for
most merchants (RBI rule) — PhonePe batches the day's transactions per merchant and credits the
settlement bank account next morning via NEFT/IMPS.

### 3.7 Mini-app / super-app runtime

PhonePe hosts third-party services (e.g., Ola, Swiggy, RedBus) as JS mini-apps inside its container,
similar to WeChat / Paytm. The runtime provides a JS sandbox, native bridge (camera, location,
payments), and a payment SDK so mini-apps can invoke PhonePe's payment flow.

### 3.8 Indus Appstore

PhonePe's separate Android app store (launched 2024) is a significant subsystem: app catalog,
developer console, APK hosting on CDN, update checks, and an in-app purchase layer. Architecturally
it's a content + commerce platform layered onto the payments core.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │    User      │     │   Merchant   │     │   VPA            │
   │ - id         │     │ - id         │     │ - id             │
   │ - phone      │     │ - name       │     │ - user_id (or    │
   │ - kyc_status │     │ - vpa        │     │   merchant_id)   │
   │ - device_id  │     │ - settlement │     │ - handle (rohit@ │
   │              │     │   _bank_acct │     │   ybl)           │
   └──────────────┘     └──────────────┘     │ - bank_account   │
                                             │   (cached)       │
                                             └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │                      TRANSACTION                             │
   │ - id (uuid)                                                  │
   │ - user_id, counterparty (vpa / merchant_id)                  │
   │ - amount (paisa, integer), currency                          │
   │ - type (P2P / P2M / COLLECT / REFUND)                        │
   │ - status (INITIATED/PROCESSING/SUCCESS/FAILED/PENDING)       │
   │ - idempotency_key (unique)                                   │
   │ - rrn (NPCI reference number)                                │
   │ - payer_psp, payee_psp, payer_bank, payee_bank               │
   │ - created_at, updated_at, settled_at                         │
   └────────────────────┬─────────────────────────────────────────┘
                        │ 1
                        ▼ *
               ┌──────────────────┐
               │  Transaction     │   (event-sourced log)
               │  Event           │
               │ - txn_id         │
               │ - event_type     │
               │ - payload (JSON) │
               │ - ts             │
               └──────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │                    SETTLEMENT BATCH                          │
   │ - id                                                          │
   │ - merchant_id                                                 │
   │ - settlement_date (T+1)                                       │
   │ - total_amount, total_txns                                   │
   │ - status (PENDING/SETTLED/FAILED)                            │
   │ - bank_reference                                              │
   └──────────────────────────────────────────────────────────────┘
```

### 4.2 Storage choices

| Data                                | Store                          | Why                                                    |
| ----------------------------------- | ------------------------------ | ------------------------------------------------------ |
| User & Merchant profiles            | MySQL/PostgreSQL (sharded)     | Strong consistency, transactional                      |
| Transactions (live)                 | MySQL (sharded by user_id)     | Index by idempotency_key, status, user_id              |
| Transaction events                  | Kafka → S3 / warehouse         | Append-only; analytics                                 |
| VPA → bank account (cache)          | Redis                          | Hot lookup on every payment                            |
| Idempotency key → txn_id            | Redis + MySQL (unique index)   | Sub-ms check in the payment path                       |
| Fraud velocity counters             | Redis (sorted sets, counters)  | Sub-ms read in risk path                               |
| Merchant settlement batches         | MySQL + batch job              | Daily aggregation, auditable                           |
| Mini-app bundles                    | CDN + S3                       | Static assets                                          |
| Analytics / MIS                     | ClickHouse / Hive              | Columnar aggregation over billions of rows             |

### 4.3 The sharding strategy

Transactions are sharded by **user_id** (the sender). All of a user's transactions, their VPA cache,
and their fraud counters sit on the same shard. Cross-user reads (a merchant querying today's sales)
go through a separate merchant-sharded read model that's built from Kafka events — eventual
consistency is acceptable for dashboards but never for the live payment.

### 4.4 No internal ledger — and why that matters

Unlike Paytm Wallet, PhonePe does **not** maintain a double-entry ledger of balances, because it
doesn't hold money. The bank is the source of truth for balances. PhonePe only records transaction
intent and outcome. This simplifies the data model but pushes complexity into **reconciliation
against NPCI and the banks** — every night, PhonePe reconciles its transaction log against NPCI's
settlement files to catch any drift (a transaction PhonePe thinks succeeded but NPCI rejected, or
vice versa).

---

## 5. Request Flow — Making a UPI Payment on PhonePe

```
USER APP    API GW    ORCH     RISK    UPI ADAPTER   NPCI     RECEIVER BANK    KAFKA
   │           │       │         │          │           │            │           │
   │─open app─▶│       │         │          │           │            │           │
   │           │       │         │          │           │            │           │
   │─enter VPA, amount, UPI PIN (PIN never leaves device; used to sign)              │
   │           │       │         │          │           │            │           │
   │─pay ₹500─▶│       │         │          │           │            │           │
   │           │─route▶│         │          │           │            │           │
   │           │       │         │          │           │            │           │
   │           │       │─check idemp key (DB)── not seen, create txn INITIATED     │
   │           │       │         │          │           │            │           │
   │           │       │─score──▶│          │           │            │           │
   │           │       │◀──OK────┤          │           │            │           │
   │           │       │         │          │           │            │           │
   │           │       │  txn → PROCESSING                                           │
   │           │       │         │          │           │            │           │
   │           │       │─resolve VPA→bank acct (cache or bank API)                  │
   │           │       │         │          │           │            │           │
   │           │       │─build UPI req + sign────────▶│            │            │
   │           │       │                              │            │            │
   │           │       │                              │─UPI pay ─▶│            │
   │           │       │                              │            │            │
   │           │       │                              │            │─debit sender│
   │           │       │                              │            │   bank     │
   │           │       │                              │            │            │
   │           │       │                              │            │─credit────▶│
   │           │       │                              │            │   receiver │
   │           │       │                              │            │   bank     │
   │           │       │                              │            │            │
   │           │       │                              │◀──SUCCESS + RRN─────────┤
   │           │       │◀──────────SUCCESS + RRN──────┤           │            │
   │           │       │         │          │           │            │           │
   │           │       │  persist result + RRN                                        │
   │           │       │  txn → SUCCESS                                              │
   │           │       │                                                              │
   │           │       │─emit TXN_SUCCESS ────────────────────────────────────────▶│
   │           │       │                                                              │
   │◀──success + receipt─────┤         │           │            │           │       │
   │           │       │                                                              │
   │                       (both sender and receiver get SMS from their banks)       │
```

**Step-by-step:**

1. **User opens PhonePe**, taps "To Bank Account" or scans a UPI QR, enters VPA / amount.
2. **User enters UPI PIN.** The PIN never leaves the device in plaintext — it's used to generate a
   signed UPI payload (the PIN-based authentication is handled by the UPI protocol's secure
   enclave on the device + PSP bank).
3. **App calls `/pay`** with amount, counterparty VPA, rail (UPI), and an idempotency key.
4. **Orchestrator checks idempotency.** If `(user_id, idempotency_key)` exists, return cached result.
   Otherwise insert `INITIATED` row.
5. **Risk engine scores** synchronously. Block, allow, or step-up (re-PIN).
6. **Txn → `PROCESSING`.** Orchestrator resolves the VPA → bank account (cache hit in Redis, or a
   call to the partner bank API), builds the UPI payload, signs it with PSP credentials.
7. **UPI adapter calls NPCI** over the secure VPN. NPCI routes the request: payer PSP → payer bank
   → payee bank → payee PSP.
8. **Banks settle in real time.** Sender's bank debits, receiver's bank credits. NPCI returns a
   SUCCESS with an **RRN (Retrieval Reference Number)**.
9. **Orchestrator persists** the result + RRN. Txn → `SUCCESS`. Emits `TXN_SUCCESS` to Kafka.
10. **Async fan-out:** notification service sends push/SMS to both parties; merchant service credits
    settlement (T+1 batch); analytics ingests; fraud ML updates feature store.

### The timeout path

If NPCI doesn't respond within ~30s, the orchestrator marks the txn `PENDING`. A reconciliation job
polls NPCI's `txnStatus` API. Based on the rail's authoritative answer, the txn is moved to `SUCCESS`
or `FAILED`. If money moved but PhonePe initially failed, the user sees a "delayed success" — money
left the bank but PhonePe didn't know for several seconds. This is the most common support ticket,
and the reconciliation engine is what resolves it.

---

## 6. Scaling Strategy

### 6.1 Stateless orchestrator + horizontal autoscaling

The orchestrator holds no per-transaction state in memory — all state is in the txn DB and Redis.
This lets PhonePe spin up hundreds of instances during Diwali/IPL. HPA scales on CPU + custom
(tps-per-pod) metrics.

### 6.2 Shard by user_id

Transactions, VPA cache, and fraud counters are co-located per user shard. A user's payment touches
only one shard, keeping latency low and avoiding cross-shard coordination.

### 6.3 Redis for the hot path

Every payment does at least 2–3 Redis lookups: idempotency key, VPA cache, risk counters. Redis
cluster, replicated, with a write-through cache backed by MySQL.

### 6.4 Kafka absorbs spikes

Billions of txn events/month. Kafka is the buffer between the synchronous payment path and the
async consumers (notifications, analytics, ML). Consumers may lag during Diwali, but the payment
itself stays fast.

### 6.5 Connection pooling to NPCI

NPCI enforces connection limits per PSP. PhonePe maintains a pool of secure connections and queues
requests during bursts, prioritising by transaction type (P2P payments get priority over collect
requests).

### 6.6 Multi-AZ + active-active within region

Payment state is replicated synchronously within the region (RBI data residency rules forbid
cross-border replication of user financial data). Multi-region DR via async replication of
non-financial data.

### 6.7 Pre-provisioned capacity for known spikes

Diwali midnight, IPL finals, payday (1st/last of month), Amazon/Flipkart sale days — capacity is
pre-warmed hours ahead. Auto-scaling reacts to unexpected spikes but known events are
pre-provisioned.

---

## 7. Tech Stack

| Layer                          | Technology                                                              |
| ------------------------------ | ----------------------------------------------------------------------- |
| Mobile apps                    | Kotlin (Android), Swift (iOS), React Native (parts), JS mini-app runtime |
| Backend languages              | Java (Spring Boot — primary), Go (high-throughput services), Python (ML) |
| API gateway                    | NGINX + custom layer; Kubernetes ingress                                 |
| Databases                      | MySQL (sharded), PostgreSQL, Cassandra                                   |
| In-memory / cache              | Redis cluster                                                           |
| Messaging                      | Apache Kafka                                                            |
| Search                         | Elasticsearch                                                            |
| Analytics warehouse            | Hive / Spark / Presto on S3; ClickHouse for real-time                    |
| ML platform                    | Python, XGBoost / LightGBM; feature store (Feast / custom)              |
| Mini-app runtime               | Custom JS VM sandbox with native bridge                                  |
| Indus Appstore                 | Separate catalog + CDN + update service                                  |
| Observability                  | Prometheus + Grafana, ELK, OpenTelemetry tracing                         |
| Deployment                     | Kubernetes, Docker, Argo CD / Spinnaker                                  |
| Cloud                          | AWS (primary), multi-AZ                                                   |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐                ┌──────────────────────┐
   │  User App  │◀──REST + WS────▶│  Python / Node       │
   │  (browser) │                │   backend            │
   └────────────┘                │  - FastAPI / Express │
                                  │  - PostgreSQL        │
                                  │  - Redis             │
                                  └──────────┬───────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │  Mock NPCI UPI      │  ← simulate the rail
                                  │  switch (sandbox)   │
                                  └─────────────────────┘
```

### 8.2 The transaction store

```python
def pay(user_id, vpa, amount, idempotency_key):
    # 1. Idempotency check
    existing = db.execute(
        "SELECT status FROM transactions WHERE user_id=%s AND idempotency_key=%s",
        (user_id, idempotency_key))
    if existing:
        return existing  # retried call

    # 2. Create txn
    txn_id = uuid4()
    db.execute("""INSERT INTO transactions (id, user_id, counterparty, amount, status, idempotency_key)
                  VALUES (%s,%s,%s,%s,'INITIATED',%s)""",
               (txn_id, user_id, vpa, amount, idempotency_key))

    # 3. Call rail
    result = upi_adapter.pay(vpa=vpa, amount=amount, txn_id=txn_id)
    # result.status in {SUCCESS, FAILED, PENDING}

    # 4. Update txn
    db.execute("UPDATE transactions SET status=%s, rrn=%s WHERE id=%s",
               (result.status, result.rrn, txn_id))

    return result
```

### 8.3 Idempotency with a unique index

```sql
CREATE UNIQUE INDEX uniq_idemp ON transactions (user_id, idempotency_key);
```

A retried call with the same key violates the unique constraint → your code catches it and returns
the existing row. Bulletproof even under concurrent retries.

### 8.4 Mock NPCI

Build a fake `/upi/pay` that:
- Returns SUCCESS 90% of the time.
- Returns FAILED 5%.
- Times out 5% (no response) — this forces you to build the reconciliation loop.

```python
def recon_loop():
    while True:
        pending = db.execute("SELECT id, rrn FROM transactions WHERE status='PENDING' AND updated_at < now() - interval '1 minute'")
        for txn in pending:
            status = upi_adapter.txn_status(txn.rrn)
            db.execute("UPDATE transactions SET status=%s WHERE id=%s", (status, txn.id))
        time.sleep(10)
```

### 8.5 What you'll learn

- How to build an orchestration layer over an external, uncontrollable rail.
- Why idempotency keys + unique indexes are the bedrock of payments.
- The `PENDING` state and why reconciliation is the hardest part of UPI.
- Why read-heavy and write-heavy paths need different storage (Redis vs MySQL).

---

## 9. Key Design Decisions & Trade-offs

| Decision                                       | Alternative considered        | Why PhonePe chose it                                          |
| ---------------------------------------------- | ----------------------------- | ------------------------------------------------------------- |
| **UPI-native, no wallet default**              | Wallet-first                  | UPI is the dominant Indian rail; no balance custody complexity |
| **Stateless orchestrator**                     | Stateful per-txn worker       | Horizontal autoscaling during spikes                          |
| **Shard by user_id**                           | Shard by txn_id               | Co-locate all user state; single-shard ACID for user ops      |
| **Synchronous risk check**                     | Async post-hoc review only    | Block fraud before money leaves                               |
| **Reconciliation against NPCI nightly**        | Trust in-app state            | NPCI is source of truth; catch silent drift                   |
| **Event-sourced txn history (Kafka)**          | Only mutable rows             | Replay, audit, decoupled consumers                            |
| **T+1 merchant settlement (RBI rule)**         | Instant settlement            | Compliance + batch efficiency                                 |
| **Idempotency key on every write**             | Trust client dedup            | Mobile networks are flaky; double-charge is catastrophic       |

### The deepest trade-off

**PhonePe is an orchestrator, not a custodian.** It cannot control the UPI rail — it can only call
it and react. This means every payment is at the mercy of NPCI's latency, the banks' availability,
and the network in between. PhonePe absorbs this uncertainty: it hides timeouts behind the `PENDING`
state, reconciles aggressively, and shows users a clean UI even when the underlying rail is
behaving badly. The trade-off is **lack of control vs. simplicity**: PhonePe doesn't have to maintain
balances or a wallet ledger, but it also can't guarantee instant success when the rail itself is
slow.

---

## 10. Common Interview Questions

**Q1: Design PhonePe / a UPI payment app.**
Walk through the super-app, but focus on the payment core: orchestrator → UPI adapter → NPCI. Explain
that PhonePe doesn't hold money — it orchestrates over the UPI rail. Cover idempotency, the PENDING
state, and reconciliation.

**Q2: What happens when a UPI payment times out?**
The txn goes to `PENDING`. A recon job polls NPCI's status API until a terminal state. If money
moved but PhonePe initially failed, the user sees a delayed success.

**Q3: How do you prevent double-charging?**
Idempotency key (client-generated UUID) with a unique index on `(user_id, idempotency_key)`. Retried
calls return the existing result. UPI itself uses RRNs for rail-level idempotency.

**Q4: How is PhonePe different from a wallet (Paytm Wallet)?**
Wallet holds money in an internal ledger; PhonePe (UPI) moves money directly between banks via
NPCI. Wallet is faster and cheaper but regulated as a PPI; UPI is open and dominant.

**Q5: How do you handle Diwali-scale TPS?**
Stateless orchestrator autoscales. Shard by user_id. Redis for hot lookups. Kafka absorbs async
load. Pre-provision capacity for known spikes. Connection pooling to NPCI.

**Q6: How does the merchant get their money?**
T+1 settlement (RBI rule). PhonePe batches each merchant's daily transactions and credits their bank
account next morning via NEFT/IMPS. Settlement service runs as a nightly batch job.

**Q7: How does the fraud engine work?**
Synchronous in the payment path. Rules (velocity, blocklist, geo) + ML model fed by Kafka-driven
feature aggregates. Latency budget ~50ms. High-risk → block or step-up.

**Q8: Why shard by user_id?**
A user's transactions, VPA cache, and fraud counters all live together. Intra-user operations are
single-shard ACID. Reads by user are fast.

**Q9: What if NPCI itself is down?**
PhonePe degrades gracefully: payments fail with a clear error, no double-charges (because of
idempotency + the PENDING state), and a backlog of PENDING txns waits for NPCI to recover.
Reconciliation resolves them once NPCI is back.

**Q10: How would you add a new mini-app (e.g., food delivery) to PhonePe?**
The mini-app runs in PhonePe's JS sandbox with a native bridge. It uses PhonePe's payment SDK to
invoke payments. Data flows: mini-app ↔ bridge ↔ PhonePe backend ↔ mini-app's own backend (via
allowed API allowlist). Architecturally like WeChat mini-programs.

---

## Further reading

- PhonePe Engineering Blog (blog.phonepe.com) — UPI, scaling, fraud posts.
- NPCI UPI documentation — the protocol PhonePe speaks.
- RBI UPI circulars — the regulatory framework.
- "The Architecture of UPI" talks (NPCI engineers on YouTube) — for the rail side.
- Martin Kleppmann, *Designing Data-Intensive Applications*, ch. 11 (stream processing) — for Kafka
  patterns.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

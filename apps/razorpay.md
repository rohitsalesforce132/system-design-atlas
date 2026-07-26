# Razorpay — System Design Atlas

> **One-line summary:** Razorpay is India's leading full-stack payment gateway and neobanking
> platform — a B2B payments orchestrator that lets any Indian business accept payments via UPI,
> cards, netbanking, and wallets through a single API, while handling the hardest problems in
> payments: idempotency, reconciliation across multiple rails, webhooks to merchants, refunds, and
> regulatory compliance — all with five-nines uptime because every minute of downtime costs its
> merchants real money.

---

## 1. Overview & Scale Numbers

Razorpay was founded in **2014** in Bengaluru (later Jaipur as a second base) by Harshil Mathur and
Shashank Kumar. It was the **second Indian startup admitted to Y Combinator** (after Instamojo's
precursor). The core insight: Indian businesses in 2014 had to integrate separately with each
payment rail — a different API for cards, another for netbanking, another for each bank's UPI. That
integration pain was the wedge.

Razorpay's product line expanded over the years:

- **Razorpay Payment Gateway** (2014) — the original product: one API for cards, netbanking,
  wallets, and later UPI.
- **Razorpay X** (2019) — neobanking for businesses (current accounts, payouts, vendor payments).
- **Razorpay Capital** — working-capital loans to merchants based on their payment history.
- **Razorpay Magic** (2022) — one-click checkout for end-customers across merchants.
- **Razorpay POS** — offline card/UPI acceptance hardware.

By 2024 Razorpay was processing **~$100B+ in annual payment volume**, powering payments for
companies from small D2C brands to large enterprises (IRCTC, Ather, various SaaS firms). It
dominates the **Indian startup-to-mid-market segment** of the payment gateway space.

### The scale

| Metric                                            | Approximate value                | Why it matters                                              |
| ------------------------------------------------- | --------------------------------- | ----------------------------------------------------------- |
| Merchants onboarded                               | ~10M+                             | From solo D2C sellers to enterprises                        |
| Annual payment volume                             | ~$100B+                           | Scale of money flow; fraud-risk surface                     |
| Payment success rate target                       | 99%+                              | Failed payments = lost GMV for merchants                    |
| Peak TPS (Diwali sale / IPL / Flipkart BBD)       | tens of thousands of tx/sec       | Capacity for 10x bursts; merchants' biggest days            |
| Supported rails                                   | UPI, Cards, NetBanking, Wallets, EMI, International | Each rail is a separate integration with its own failure modes |
| Webhook deliveries per day                        | tens of millions                  | Merchants depend on webhooks to fulfil orders               |
| Target end-to-end latency                         | <2 seconds                        | Includes the upstream rail round-trip                       |
| Refund processing time                            | instant to T+7 (rail-dependent)   | Refunds have their own reconciliation                       |

### The product goal in one paragraph

A merchant integrates Razorpay's checkout SDK into their website or app. When an end-customer pays,
Razorpay presents a unified payment page (UPI, card, netbanking tabs), takes the customer through
the chosen rail (UPI collect, card auth via gateway, netbanking redirect), captures the result from
the rail, settles the money to the merchant's bank account on T+1/T+2, and notifies the merchant via
webhook and dashboard. Behind the scenes, Razorpay abstracts away 10+ payment rails, handles
timeouts and retries, scores fraud, manages disputes and chargebacks, and provides a dashboard for
the merchant to track every rupee. Every payment must be idempotent, every webhook must be
delivered at-least-once, and every refund must reconcile to the paisa.

---

## 2. High-Level Architecture

Razorpay's architecture is best understood as **a payment orchestration layer that sits between the
merchant (the business) and the payment rails (NPCI, banks, card networks)**. Razorpay doesn't
custody customer money in the default flow — it **routes** money and **records** what happened.

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                        MERCHANT INTEGRATION                           │
   │   - Checkout SDK (JS / iOS / Android) embedded in merchant's site    │
   │   - Server-to-server API (merchant backend → Razorpay)               │
   │   - Webhooks (Razorpay → merchant backend)                           │
   │   - Merchant Dashboard (web UI)                                      │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │  HTTPS / TLS / signed payloads
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                         EDGE / API GATEWAY                           │
   │       (TLS, merchant auth, rate limit per key, WAF, routing)         │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
   ┌──────────┐            ┌──────────────┐           ┌──────────────┐
   │ Merchant │            │   Payment    │           │  Settlement  │
   │ & Key    │            │ Orchestration│           │  Service     │
   │ Service  │            │   (core)     │           │  (T+1/T+2)   │
   └──────────┘            └──────┬───────┘           └──────────────┘
                                   │
            ┌──────────────────────┼─────────────────────┐
            ▼                      ▼                     ▼
   ┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐
   │  Smart Router  │    │  Fraud & Risk    │   │  Webhook         │
   │  (rail picker) │    │  Engine          │   │  Dispatcher      │
   └────────┬───────┘    └──────────────────┘   └──────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                        RAIL ADAPTER LAYER                             │
   │  ┌─────┐ ┌──────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ ┌───────────┐  │
   │  │ UPI │ │ Card │ │ NetBank  │ │ Wallets  │ │ EMI  │ │ Internat. │  │
   │  │(NPCI)│ │(Visa/│ │ (each    │ │(Paytm/   │ │      │ │ (Cards /  │  │
   │  │      │ │ MC / │ │  bank)   │ │ PhonePe) │ │      │ │ PayPal)   │  │
   │  │      │ │RuPay)│ │          │ │          │ │      │ │           │  │
   │  └─────┘ └──────┘ └──────────┘ └──────────┘ └──────┘ └───────────┘  │
   └──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          EVENT / DATA PLANE                           │
   │   Kafka (txn events) → Analytics, Settlement, Webhooks, ML, Audit    │
   └──────────────────────────────────────────────────────────────────────┘
```

### The single most important idea

**Razorpay is a router, an orchestrator, and a reconciliation engine.** It does not invent new
payment methods; it makes 10+ existing methods usable through one consistent API. The hard parts
are:

1. **Abstracting** away each rail's quirks (UPI's PENDING state, card auth flows, netbanking
   redirects, wallet balances).
2. **Routing** payments intelligently (which rail has the highest success rate for this amount?
   this issuer bank? this time of day?).
3. **Reconciling** against each rail and the settlement files from acquiring banks.

### The key abstraction: the Payment State Machine

```
   [created] ──attempt──▶ [attempted] ──rail auth──▶ [authorized]
                                                        │
                                       ┌────────────────┴───────────────┐
                                       ▼                                ▼
                                  [captured]                      [failed]
                                       │
                                       ▼
                                  [refunded] (full / partial)
```

Razorpay splits the traditional "payment" into two distinct phases — **authorize** (money reserved)
and **capture** (money moved) — mirroring the card network's auth/capture flow. This is invisible to
the end-customer in a typical UPI flow but matters for cards, international, and some merchant
flows.

---

## 3. Detailed Component Breakdown

### 3.1 Merchant & Key service

Owns merchant accounts, API keys (key_id + key_secret, HMAC-signed requests), webhook URLs, KYC
status (mandatory under RBI for payment aggregators), and merchant configuration (supported rails,
settlement bank account, theme/branding for the checkout). API keys are HMAC-validated at the edge
for every request.

### 3.2 Payment Orchestration service (the conductor)

Receives a payment request from the merchant (server-to-server) or the checkout SDK (from the
end-customer's browser), drives the rail, captures the result, and emits events. Stateless and
horizontally scalable.

```
   payment request arrives
        │
        ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  1. Idempotency check (merchant_id + idempotency_key)         │
   │  2. Risk score (synchronous)                                  │
   │  3. Pick rail (smart router)                                  │
   │  4. Call rail adapter                                         │
   │  5. On success: persist payment + rail reference              │
   │  6. Emit payment.captured event                               │
   │  7. Trigger webhook dispatcher (async)                        │
   │  8. Return to merchant / SDK                                  │
   └───────────────────────────────────────────────────────────────┘
```

### 3.3 Smart Router (rail picker)

Razorpay's secret sauce for success-rate optimisation. Given a payment request, the smart router
picks the best rail (or the best acquiring bank for cards) based on:

- **Rail availability** — is UPI up right now? Is a specific card gateway timing out?
- **Issuer-bank affinity** — this customer's card issuing bank has higher success via acquiring
  bank A than bank B (learned from historical data).
- **Amount** — small amounts → UPI; large → card; EMI for big-ticket.
- **Customer preference** — if the customer picks UPI explicitly, that overrides.

```
   ┌────────────────────────────────────────────────────────────────┐
   │                     Smart Router                               │
   │                                                                │
   │   inputs: rail requested?, amount, issuer_bank, history, time  │
   │                                                                │
   │   ┌─────────────────────────────────────────────────────────┐  │
   │   │  ML model: success-rate predictor per rail x issuer x $ │  │
   │   └─────────────────────┬───────────────────────────────────┘  │
   │                         │                                      │
   │                         ▼                                      │
   │            pick rail with highest expected success             │
   │            (fallback to second-best if first fails)            │
   └────────────────────────────────────────────────────────────────┘
```

This is what makes Razorpay's success rate higher than naive integrations — **fallback routing**.
If UPI fails for a particular issuer, the router can retry via a different UPI acquirer or fall back
to a card-on-file flow.

### 3.4 Rail adapter layer

Abstracts the external payment rails. Each adapter speaks the rail's protocol and normalises the
response into Razorpay's internal result codes. The layer must handle:

- **Different protocols** — UPI is synchronous-ish (collect → response in seconds); netbanking is a
  **redirect flow** (customer leaves Razorpay, goes to their bank's site, comes back with a
  callback); cards involve multiple hops (gateway → network → issuer).
- **Timeouts** — each rail has its own SLA. Netbanking can take 30s+; UPI is ~5s.
- **Async status queries** — when a rail times out, the adapter must query the rail's status API
  later (the `pending` → `success`/`failed` reconciliation path).

```
   ┌─────────────────────────────────────────────────────────┐
   │                  Rail Adapter Layer                     │
   │                                                         │
   │   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐ │
   │   │  UPI    │  │  Card   │  │  Net    │  │ Wallet   │ │
   │   │ Adapter │  │ Adapter │  │ Banking │  │ Adapter  │ │
   │   │         │  │         │  │ Adapter │  │          │ │
   │   │ collect │  │ auth +  │  │ redirect│  │ balance  │ │
   │   │ status  │  │ capture │  │ callback│  │ debit    │ │
   │   └─────────┘  └─────────┘  └─────────┘  └──────────┘ │
   └─────────────────────────────────────────────────────────┘
```

### 3.5 Settlement service

Merchants don't get money instantly — Razorpay aggregates each merchant's captured payments for the
day and **settles** to their bank account on T+1 or T+2 (configurable, RBI-regulated). This involves:

1. A daily batch job per merchant summing captured payments minus refunds minus Razorpay fees.
2. A payout to the merchant's bank account via NEFT/IMPS.
3. A settlement record the merchant can see in their dashboard.

```
   Merchant's day:
   ─────────────────────────────────────────────────────
   00:00        09:00          18:00        23:59
     │            │              │            │
     ▼            ▼              ▼            ▼
   payments stream in all day, captured one by one
     │
     │  at end of day (or next morning):
     ▼
   ┌──────────────────────────────────────────────┐
   │  Settlement batch:                            │
   │   sum(captured) - sum(refunded) - fees = net │
   │  → NEFT/IMPS payout to merchant bank acct     │
   │  → settlement record created                  │
   └──────────────────────────────────────────────┘
```

### 3.6 Webhook Dispatcher

One of the most operationally important components. When a payment is captured, Razorpay must tell
the merchant's backend so they can fulfil the order. Webhooks are **HTTP POST callbacks** to a
merchant-configured URL.

Key requirements:

- **At-least-once delivery** — webhooks may be sent multiple times; merchants must be idempotent.
- **Retry with backoff** — if the merchant's server is down, retry after 1s, 5s, 30s, 2m, 10m, 1h...
- **Signature** — every webhook is HMAC-signed so the merchant can verify authenticity.
- **Dead-letter queue** — if all retries fail, the event lands in a DLQ for manual / dashboard
  replay.

```
   payment.captured event
        │
        ▼
   ┌─────────────────────────────────────────────────────────┐
   │  Webhook Dispatcher                                     │
   │                                                         │
   │   POST merchant_url                                    │
   │     body: event payload                                 │
   │     header: X-Razorpay-Signature: <HMAC>                │
   │                                                         │
   │   if 200 OK  → done                                     │
   │   if non-200 or timeout → retry (exponential backoff)   │
   │                                                         │
   │   after N retries → dead-letter queue (dashboard)       │
   └─────────────────────────────────────────────────────────┘
```

### 3.7 Fraud & Risk engine

Scores every payment in real time. Uses rules + ML. For B2B payments, fraud patterns differ from
consumer apps:

- **Card testing** — fraudster tries stolen cards against a merchant; velocity per IP/device.
- **Friendly fraud / chargebacks** — customer disputes a legitimate payment.
- **Merchant-side fraud** — a fraudulent merchant onboarded to launder money.

High-risk payments are blocked or held for manual review.

### 3.8 Merchant Dashboard & Analytics

A web UI where merchants see payments, settlements, refunds, dispute management, customer insights,
and configuration. Backed by a read-optimised store (ClickHouse for fast aggregation over billions
of rows).

### 3.9 Razorpay Magic

A cross-merchant one-click checkout: a customer's saved details (UPI ID, card token) are reused
across any Razorpay-powered merchant, with a single OTP/auth. Architecturally this is a **shared
customer profile layer** above the per-merchant payment flow.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │  Merchant    │     │  Customer    │     │   Payment        │
   │ - id         │     │  (end-user)  │     │ - id             │
   │ - name       │     │ - id         │     │ - merchant_id    │
   │ - api_key_id │     │ - phone      │     │ - customer_id    │
   │ - api_secret │     │ - email      │     │ - amount (paisa) │
   │ - webhook_url│     │ - (per merch)│     │ - currency       │
   │ - settlement │     └──────────────┘     │ - rail           │
   │   _bank_acct │                          │ - status         │
   │ - kyc_status │                          │   (created/      │
   └──────────────┘                          │    attempted/    │
                                              │    authorized/   │
                                              │    captured/     │
   ┌──────────────────────────────────┐       │    failed/       │
   │   Settlement                     │       │    refunded)     │
   │ - id                             │       │ - idempotency_key│
   │ - merchant_id                    │       │ - rail_ref (RRN) │
   │ - date (T+1/T+2)                 │       │ - fee, tax       │
   │ - gross_amount                   │       │ - created_at     │
   │ - refunds                        │      └────────┬──────────┘
   │ - fees                           │               │ 1
   │ - net_amount                     │               │
   │ - status (pending/settled)       │               ▼ *
   │ - bank_reference                 │      ┌──────────────────┐
   └──────────────────────────────────┘      │  Payment Event   │
                                              │ - payment_id     │
   ┌──────────────────────────────────┐       │ - event_type     │
   │   Refund                          │       │   (authorized/   │
   │ - id                              │       │    captured/     │
   │ - payment_id                      │       │    refunded)     │
   │ - amount                          │       │ - payload        │
   │ - status                          │       │ - ts             │
   │   (pending/processed/failed)      │      └──────────────────┘
   │ - rail_ref                        │
   └──────────────────────────────────┘
```

### 4.2 Storage choices

| Data                                | Store                          | Why                                                    |
| ----------------------------------- | ------------------------------ | ------------------------------------------------------ |
| Merchant accounts                   | MySQL/PostgreSQL (sharded)     | Strong consistency, transactional                      |
| API keys                            | MySQL + Redis cache            | HMAC validation at edge; cached for sub-ms lookup      |
| Payments (live)                     | MySQL (sharded by merchant_id) | Index by idempotency_key, merchant, status             |
| Payment events                      | Kafka → S3 / warehouse         | Append-only; analytics + webhook source                |
| Refunds                             | MySQL + event log              | Auditable                                              |
| Settlement batches                  | MySQL + batch job              | Daily aggregation                                       |
| Webhook delivery log                | MySQL + Redis (retry queue)    | At-least-once delivery + retry state                    |
| Fraud velocity counters             | Redis                          | Sub-ms read in risk path                               |
| Smart router success-rate model     | Redis (real-time counts) + ML model | Per rail × issuer × amount stats                 |
| Merchant dashboard / analytics      | ClickHouse                     | Columnar, fast aggregation over billions of rows       |

### 4.3 The sharding strategy

Payments are sharded by **merchant_id**. All of a merchant's payments, refunds, and settlements
live on one shard, so merchant-facing queries (dashboard, settlements) are single-shard and fast.

### 4.4 Idempotency at the API boundary

Every Razorpay API call accepts an optional `idempotency_key` (or generates one). The payment table
has a unique index on `(merchant_id, idempotency_key)`. A retried call hits the same row and returns
the existing payment instead of creating a new one.

```sql
CREATE UNIQUE INDEX uniq_merchant_idemp
ON payments (merchant_id, idempotency_key);
```

This is the single most important pattern in the system. Without it, a single network hiccup could
double-charge an end-customer.

---

## 5. Request Flow — Processing a Razorpay Payment Gateway Transaction

```
CUSTOMER   CHECKOUT SDK   RZP API GW   ORCHESTRATOR   SMART ROUTER   RAIL ADAPTER   KAFKA   WEBHOOK SVC
   │            │             │             │              │              │            │          │
   │─click pay──▶             │             │              │              │            │          │
   │            │             │             │              │              │            │          │
   │            │─create order (server-to-server, merchant → Razorpay, returns order_id)         │
   │            │             │             │              │              │            │          │
   │─pick UPI──▶│             │             │              │              │            │          │
   │  enter VPA │             │             │              │              │            │          │
   │            │             │             │              │              │            │          │
   │            │─POST /payments (order_id, rail:UPI, VPA, amount)                                   │
   │            │             │─route───────▶│              │              │            │          │
   │            │             │             │              │              │            │          │
   │            │             │             │─check idemp key (DB) ── not seen, create payment created          │
   │            │             │             │              │              │            │          │
   │            │             │             │─risk score (sync) ── OK                                 │
   │            │             │             │              │              │            │          │
   │            │             │             │─pick rail ──▶│              │            │          │
   │            │             │             │◀──UPI best──┤              │            │          │
   │            │             │             │              │              │            │          │
   │            │             │             │  payment → attempted                                     │
   │            │             │             │─call UPI adapter─────────▶│            │          │
   │            │             │             │              │              │            │          │
   │            │             │             │              │              │─UPI collect req      │
   │            │             │             │              │              │  → NPCI               │
   │            │             │             │              │              │  → banks              │
   │            │             │             │              │              │            │          │
   │            │             │             │              │              │◀──SUCCESS + RRN──────┤
   │            │             │             │◀──SUCCESS + RRN────────────┤            │          │
   │            │             │             │              │              │            │          │
   │            │             │             │  payment → captured                                      │
   │            │             │             │  persist payment + rail_ref                              │
   │            │             │             │              │              │            │          │
   │            │             │             │─emit payment.captured ────────────────────────────▶│
   │            │             │             │                                                        │
   │            │             │             │                                                        │
   │◀──payment success + receipt (SDK)──────┤              │              │            │          │
   │            │             │             │                                                        │
   │                                                                                              │
   │                                            (async: webhook dispatcher fires)                 │
   │                                                                                              │
   │                                            WEBHOOK SVC ──POST merchant_url (signed)──▶ merchant│
   │                                                        ◀──200 OK── merchant backend          │
   │                                                                                              │
   │   (end of day: settlement batch sums captured payments, pays out merchant T+1/T+2)            │
```

**Step-by-step:**

1. **Merchant creates an order** server-to-server: `POST /orders` with amount + currency. Razorpay
   returns an `order_id`. This separates "intent to collect" from "actual payment" — useful for
   analytics and refunds.
2. **Customer opens checkout SDK.** The SDK loads Razorpay's hosted checkout page (or a custom
   integration), pre-filled with the order details.
3. **Customer picks UPI, enters VPA.** SDK calls `POST /payments` with `order_id`, `rail: UPI`,
   `vpa`, and amount.
4. **Orchestrator checks idempotency.** Looks up `(merchant_id, idempotency_key)`. If found,
   returns the existing payment. Otherwise inserts a `created` payment.
5. **Risk engine scores** synchronously. If blocked → payment `failed`, merchant notified.
6. **Smart router picks the rail.** Even though the customer chose UPI, the router may pick which
   acquiring bank to route through (Razorpay has multiple UPI PSP partnerships for redundancy).
7. **Payment → `attempted`.** Orchestrator calls the UPI adapter, which calls NPCI.
8. **UPI collect request** flows to NPCI → remitter bank → beneficiary bank. Customer approves via
   UPI PIN on their bank's app. NPCI returns SUCCESS with an RRN.
9. **Payment → `captured`.** Orchestrator persists the rail reference (RRN) and amount.
   `payment.captured` event emitted to Kafka.
10. **Webhook dispatcher fires** asynchronously: POSTs the event (HMAC-signed) to the merchant's
    webhook URL. Retries with backoff if the merchant's server is down.
11. **Customer sees success** in the checkout SDK; merchant can now fulfil the order.
12. **End of day:** Settlement service batches the merchant's captured payments, subtracts refunds
    and Razorpay fees, and pays out the net to the merchant's bank account on T+1/T+2.

### The failure / timeout path

If the rail times out, the orchestrator marks the payment `pending`. A reconciliation job queries
the rail's status API. Based on the rail's authoritative answer, the payment moves to `captured`
(late capture) or `failed`. If money moved but the initial response failed, the merchant is notified
via a delayed `payment.captured` webhook.

### Refund flow

```
   merchant requests refund (payment_id, amount)
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │  Refund Service                              │
   │  - create refund record (status: pending)    │
   │  - call rail's refund API (UPI refund / card │
   │    refund to original instrument)            │
   │  - on success: refund → processed            │
   │  - emit refund.processed                     │
   │  - webhook to merchant                       │
   │  - settlement service deducts from next      │
   │    merchant payout                           │
   └──────────────────────────────────────────────┘
```

Refunds are always to the **original instrument** (RBI rule). The original payment's rail reference
is used to initiate the reverse transaction.

---

## 6. Scaling Strategy

### 6.1 Stateless orchestrator + horizontal autoscaling

The orchestrator holds no per-payment state in memory — all state is in the payment DB and Redis.
HPA scales on CPU + custom (tps-per-pod) metrics. Pre-provisioned capacity for Diwali / IPL /
Flipkart Big Billion Days.

### 6.2 Shard by merchant_id

Payments, refunds, and settlements are co-located per merchant shard. Merchant-facing queries are
single-shard and fast.

### 6.3 Redis for the hot path

Every payment does several Redis lookups: API key validation, idempotency cache, risk velocity
counters, smart router success-rate stats. Redis cluster, replicated.

### 6.4 Kafka absorbs spikes + decouples consumers

Every payment emits events. Consumers (webhook dispatcher, settlement, analytics, ML) are decoupled
— a webhook outage doesn't break the payment path; events queue in Kafka. During Diwali, consumers
may lag but payments themselves stay fast.

### 6.5 Webhook dispatcher scaled independently

Webhooks are HTTP fan-out to millions of merchant endpoints, some slow, some down. The dispatcher
is a separate fleet of workers pulling from a retry queue, isolated from the payment path. Each
merchant's webhook endpoint has its own retry budget.

### 6.6 Rail connection pooling

Each rail (NPCI, card gateways, each netbanking bank) has its own connection pool with limits.
Razorpay maintains multiple acquiring bank partnerships per rail so a single bank outage doesn't
take down a whole rail.

### 6.7 Multi-AZ + DR

RBI's data-localisation rules require Indian financial data to stay in India. Razorpay runs
multi-AZ within an Indian region, with async DR replication for non-financial data.

### 6.8 Smart router = graceful degradation

When UPI is degraded, the smart router shifts traffic to cards or wallets. When one card acquiring
bank is down, traffic shifts to another. This **active-active across acquirers** is what keeps
Razorpay's aggregate success rate high even when individual rails flake.

---

## 7. Tech Stack

| Layer                          | Technology                                                              |
| ------------------------------ | ----------------------------------------------------------------------- |
| Checkout SDK                   | JavaScript (web), Kotlin (Android), Swift (iOS), React Native (parts)   |
| Backend languages              | Java (Spring Boot — primary), Go (high-throughput), Python (ML / data)  |
| API gateway                    | NGINX + custom layer; Kubernetes ingress                                 |
| Databases                      | MySQL (sharded), PostgreSQL, Cassandra (some hot KV)                     |
| In-memory / cache              | Redis cluster                                                           |
| Messaging                      | Apache Kafka                                                            |
| Search                         | Elasticsearch (merchant support, dashboard search)                      |
| Analytics warehouse            | ClickHouse (real-time), Hive / Spark on S3 (batch)                       |
| ML platform                    | Python, XGBoost / LightGBM; feature store                                |
| Webhook delivery               | Custom worker fleet + retry queue (Kafka + Redis)                        |
| Observability                  | Prometheus + Grafana, ELK, OpenTelemetry tracing                        |
| Deployment                     | Kubernetes, Docker, Argo CD / Spinnaker                                  |
| Cloud                          | AWS India (primary), multi-AZ                                            |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌──────────────┐                ┌──────────────────────┐              ┌──────────────┐
   │  Checkout JS │◀────REST──────▶│  Python / Node       │──REST───────▶│ Merchant     │
   │  (browser)   │                │   backend            │              │ Webhook recv │
   └──────────────┘                │  - FastAPI / Express │              └──────────────┘
                                    │  - PostgreSQL        │
                                    │  - Redis             │
                                    │  - Kafka (optional)  │
                                    └──────────┬───────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │   Mock rail adapter │  ← simulate UPI / card
                                    │   (sandbox)         │
                                    └─────────────────────┘
```

### 8.2 The payment store with idempotency

```python
def create_payment(merchant_id, order_id, amount, rail, idempotency_key):
    # Idempotency check
    existing = db.execute(
        "SELECT id, status FROM payments WHERE merchant_id=%s AND idempotency_key=%s",
        (merchant_id, idempotency_key))
    if existing:
        return existing  # retried call

    payment_id = f"pay_{uuid4().hex}"
    try:
        db.execute("""INSERT INTO payments
                      (id, merchant_id, order_id, amount, rail, status, idempotency_key)
                      VALUES (%s,%s,%s,%s,%s,'created',%s)""",
                   (payment_id, merchant_id, order_id, amount, rail, idempotency_key))
    except UniqueViolation:
        # concurrent retry won the race; return the existing row
        return db.execute("SELECT id, status FROM payments WHERE merchant_id=%s AND idempotency_key=%s",
                          (merchant_id, idempotency_key))

    # Call rail
    result = rail_adapter.charge(rail, amount, payment_id)
    db.execute("UPDATE payments SET status=%s, rail_ref=%s WHERE id=%s",
               (result.status, result.rail_ref, payment_id))
    return result
```

### 8.3 Smart router (simple version)

```python
def pick_rail(amount, customer_history):
    # Use rolling success rates per rail from Redis
    upi_success = redis.hget("rail_stats:upi", "success_rate") or 0.95
    card_success = redis.hget("rail_stats:card", "success_rate") or 0.90

    if amount < 2000 and upi_success > 0.90:
        return "UPI"
    return "CARD"

def update_rail_stats(rail, success):
    # called after each payment attempt
    redis.hincrby(f"rail_stats:{rail}", "attempts", 1)
    if success:
        redis.hincrby(f"rail_stats:{rail}", "successes", 1)
    # recompute success_rate periodically
```

### 8.4 Webhook dispatcher

```python
import hashlib, hmac, time

def dispatch_webhook(merchant_url, secret, event_payload):
    body = json.dumps(event_payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": signature, "Content-Type": "application/json"}

    for attempt, delay in enumerate([1, 5, 30, 120, 600, 3600]):
        try:
            r = requests.post(merchant_url, data=body, headers=headers, timeout=10)
            if r.status_code == 200:
                return  # delivered
        except requests.RequestException:
            pass
        time.sleep(delay)

    # all retries failed → dead-letter queue
    db.execute("INSERT INTO webhook_dlq (url, payload) VALUES (%s, %s)", (merchant_url, body))
```

### 8.5 Settlement batch (simplified)

```python
def run_settlement(merchant_id, date):
    captured = db.execute("""SELECT COALESCE(SUM(amount),0) AS total
                             FROM payments WHERE merchant_id=%s AND status='captured'
                             AND DATE(created_at)=%s""", (merchant_id, date))
    refunded = db.execute("""SELECT COALESCE(SUM(amount),0) AS total
                             FROM refunds r JOIN payments p ON r.payment_id=p.id
                             WHERE p.merchant_id=%s AND r.status='processed'
                             AND DATE(r.created_at)=%s""", (merchant_id, date))
    fee = int(captured * 0.02)  # 2% MDR
    net = captured - refunded - fee

    payout_ref = bank_api.neft_payout(merchant_bank_acct, net)
    db.execute("""INSERT INTO settlements (merchant_id, date, gross, refunds, fees, net, bank_ref)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)""",
               (merchant_id, date, captured, refunded, fee, net, payout_ref))
```

### 8.6 Mock rail adapter

```python
def charge(rail, amount, payment_id):
    if rail == "UPI":
        # simulate UPI collect
        if random.random() < 0.95:
            return Result(status="captured", rail_ref=f"rrn_{payment_id}")
        elif random.random() < 0.5:
            return Result(status="failed", rail_ref=None)
        else:
            return Result(status="pending", rail_ref=None)  # forces reconciliation
    # ... card, netbanking ...
```

### 8.7 What you'll learn

- How to build an orchestration layer over multiple external rails.
- Why idempotency keys + unique indexes are non-negotiable in payments.
- The auth/capture split and why it maps to card-network semantics.
- How webhook at-least-once delivery + HMAC signatures work.
- How T+1 settlement batches aggregate per merchant.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                       | Alternative considered         | Why Razorpay chose it                                        |
| ---------------------------------------------- | ------------------------------ | ------------------------------------------------------------ |
| **Auth/capture split**                         | Single "paid" state            | Mirrors card-network semantics; supports holds + partial capture |
| **Shard by merchant_id**                       | Shard by payment_id            | Co-locate merchant state; merchant-facing queries are single-shard |
| **Smart router with fallback**                 | Static rail per request        | Higher aggregate success rate; rail redundancy                |
| **Stateless orchestrator**                     | Stateful per-payment worker    | Horizontal autoscaling during Diwali                          |
| **At-least-once webhooks + HMAC**              | At-most-once / no signature    | Merchants must know about captures; signature prevents spoofing |
| **T+1/T+2 settlement batches**                 | Instant settlement             | Compliance + batch efficiency; cheaper payouts               |
| **Idempotency key on every API**               | Trust client dedup             | Network flakiness; double-charge is catastrophic              |
| **Multi-acquirer active-active per rail**      | Single acquirer per rail       | One bank outage shouldn't take down a whole rail              |

### The deepest trade-off

**Latency vs. comprehensiveness.** Razorpay could be faster if it supported only one rail (say,
UPI-only) with a thin integration. Instead it supports 10+ rails, each with its own quirks, because
**merchants want choice and fallback**. Every additional rail adds latency (more code paths,
more potential timeouts) but increases the aggregate success rate and the merchant's conversion.
Razorpay chose comprehensiveness — it is a router first, a payments company second. The smart
router is the engineering expression of that choice: route around individual rail failures to keep
the merchant's conversion high.

---

## 10. Common Interview Questions

**Q1: Design a payment gateway like Razorpay.**
Walk through merchant → checkout SDK → orchestrator → smart router → rail adapter. Explain auth/
capture. Cover idempotency, the pending state, webhooks, and T+1 settlement. Emphasise that
Razorpay is an orchestrator over multiple rails.

**Q2: How do you prevent double-charging?**
Idempotency key per API call, unique-indexed on `(merchant_id, idempotency_key)`. Retries return the
existing payment. The rail adapter also uses rail-level idempotency (UPI RRN, card network's
request_id).

**Q3: What happens when a payment times out at the rail?**
Payment goes to `pending`. A reconciliation job queries the rail's status API. Based on the
authoritative answer, the payment moves to `captured` (late webhook fires) or `failed` (refund if
money moved).

**Q4: How do webhooks work, and how do you guarantee delivery?**
At-least-once: Razorpay retries with exponential backoff (1s → 1h) until the merchant returns 200.
HMAC signature lets the merchant verify authenticity. After N retries, the event goes to a
dead-letter queue replayable from the dashboard.

**Q5: What's auth vs. capture, and why split them?**
Auth = reserve money (card network holds it). Capture = actually move it. Splitting supports
flows like "authorise on order, capture on shipment" (common in e-commerce). UPI doesn't have a
native auth/capture split, so Razorpay simulates it.

**Q6: How does the smart router improve success rate?**
Picks the rail (or acquiring bank) with the highest predicted success for this amount × issuer ×
time. Falls back to a second-best rail if the first fails. Active-active across acquirers means a
single bank outage doesn't tank a whole rail.

**Q7: How do settlements work?**
T+1/T+2 batches. Each merchant's captured payments for the day are summed, refunds and fees
subtracted, and the net is paid out via NEFT/IMPS to the merchant's bank account. A settlement
record is created for the dashboard.

**Q8: How do refunds work?**
Always to the original instrument (RBI rule). Razorpay calls the rail's refund API using the
original payment's rail reference. Refund status is tracked; the settlement service deducts the
refund from the merchant's next payout.

**Q9: How do you scale to Diwali-level TPS?**
Stateless orchestrator autoscales. Shard by merchant_id. Redis for hot lookups. Kafka absorbs async
load (webhooks, settlement, analytics). Pre-provision capacity for known spikes. Multi-acquirer
redundancy per rail.

**Q10: Why shard by merchant_id and not by payment_id?**
A merchant's payments, refunds, and settlements are queried together (dashboard, settlement batch).
Sharding by merchant_id keeps these single-shard. Payment_id sharding would scatter a merchant's
data across shards, making aggregation slow.

**Q11: How do you handle a fraudulent merchant?**
Onboarding KYC (PAN, GST, business proof). Real-time monitoring of chargeback rates and refund
patterns. High-risk merchants are held for manual review or have settlements frozen.

---

## Further reading

- Razorpay Engineering Blog (razorpay.com/blog/engineering) — smart router, scaling, webhooks.
- Razorpay API documentation — the surface area of the orchestrator.
- NPCI UPI documentation — the dominant rail Razorpay routes through.
- RBI Payment Aggregator guidelines — the regulatory framework.
- Stripe Engineering Blog — for parallel patterns (Stripe and Razorpay solve very similar problems).

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

# Flipkart — System Design Atlas

> **One-line summary:** Flipkart is India's largest homegrown e-commerce marketplace, handling
> hundreds of millions of customers, a million-plus sellers, and the annual **Big Billion Days**
> sale that pushes the platform past **100,000 orders per second** — all served from
> India-resident data centres with a mix of microservices, JVM-heavy tech, and an in-house
> fulfillment network (eKart) optimised for COD-heavy, UPI-heavy, price-sensitive Indian traffic.

---

## 1. Overview & Scale Numbers

If you understand Amazon (see `amazon.md`), Flipkart is the **India-shaped version** of the same
problem — but with three twists that make the engineering genuinely different:

1. **Cash-on-delivery (COD) is still ~40–60% of orders** in many categories. You can't "authorise
   a charge" when there is no charge — so the order pipeline must trust the customer and reconcile
   later.
2. **UPI rules.** Over 70% of prepaid orders flow through UPI (PhonePe, Google Pay, Paytm,
   Flipkart's own super.app). UPI is asynchronous, push-based, and has its own failure modes
   (collect-request timeouts, bank downtime, NPCI switches).
3. **Big Billion Days (BBD).** A 6–8 day Diwali sale that generates **10–20× the normal traffic**.
   Flipkart has publicly talked about crossing **1 billion visits** and **tens of millions of units
   shipped** during a single BBD. Affordability products (EMI, BNPL via super.app) and
   exchange/offers engines are stressed harder than at any other time.

### The numbers

| Metric                                        | Approximate value              | Why it matters                                              |
| --------------------------------------------- | ------------------------------ | ---------------------------------------------------------- |
| Registered customers                          | ~500M+                         | Drives multi-datacentre, multi-AZ deployment in India      |
| Monthly active users (app)                    | ~250M+                         | Mobile-first; ~90% of GMV is from the Android app          |
| Sellers                                       | ~1.4M+                         | Marketplace model; seller trust + fraud is non-trivial     |
| Listings (SKUs)                               | 150M+ across categories        | Heavy on mobiles, large appliances, fashion                |
| Pin codes serviceable                         | 100% of serviceable India      | Last-mile in Tier-2/3/4 cities with mixed courier networks |
| Orders per second at BBD peak                 | ~100,000+                      | Comparable to Amazon Prime Day in a single country         |
| Concurrent users on app during BBD            | ~10–15M                        | Pushes the WebSocket/notification fan-out                   |
| Fulfillment centres (eKart + sort centres)    | 100+                           | Each is a robotics + WMS facility                          |
| Delivery hubs / DaCs                           | thousands                      | Last-mile delivery stations, kirana partner network        |
| Page render budget                            | <200ms p95                     | Same as Amazon: every 100ms of latency costs sales         |
| UPI share of prepaid payments                 | >70%                           | Asynchronous payment path with retry/idempotency           |
| COD share of all orders                       | ~40–60% (category-dependent)   | No "auth at checkout" — trust + RTO risk                   |

### The product goal

A customer in **Indore** opens the Flipkart app during Big Billion Days, searches "iPhone under
₹70,000," sees ranked, in-stock results in 200ms, applies an exchange offer + a no-cost EMI coupon,
checks out via UPI, and gets a tracking link the next morning when eKart picks the phone from a
warehouse in **Bengaluru**. Behind the scenes: inventory reserved atomically, EMI eligibility
checked against a credit-bureau call, UPI collect fired and reconciled, and the order fanned out to
a fulfillment centre chosen by which one has stock *and* the cheapest shipping lane to 452001.

---

## 2. High-Level Architecture

Flipkart runs a **service-oriented architecture** (internally called the "platform" model). The
company has been public about moving from a PHP monolith (~2007–2012) to a JVM-based microservices
stack (~2013 onward) and then to a heavily platformised, data-plane/control-plane split.

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       CUSTOMER (Android app / web / iOS)             │
   │   90% of GMV is mobile. App ships with native push + deep-links.    │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │  HTTPS + MQTT/WebSocket for live data
                                  ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │        EDGE: Flipkart CDN, Akamai front, WAF, DDoS, Bot defence     │
   │               (BBD attracts scalper bots — WAF is load-bearing)     │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │               API GATEWAY / BFF (Backend-for-Frontend)              │
   │   - Auth (Flipkart account / OTP)                                   │
   │   - Aggregation: a single app screen = 5–15 downstream calls        │
   │   - Rate limit, circuit break, fallback to cached fragments         │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼───────────────────────────┐
        ▼                         ▼                           ▼
   ┌───────────┐            ┌──────────────┐           ┌──────────────┐
   │  Catalog  │            │   Search     │           │   Pricing +  │
   │  Service  │            │   Service    │           │   Offers     │
   │ (product  │            │ (in-house    │           │   Engine     │
   │  metadata)│            │  + ES)       │           │ (waterfall)  │
   └─────┬─────┘            └──────────────┘           └──────────────┘
         │
   ┌─────▼──────────────────────────────────────────────────────────────┐
   │                       COMMERCE PLANE                                │
   │   Cart  •  Checkout  •  Inventory  •  Order Pipeline               │
   │   Payment (UPI/Card/COD/BNPL)  •  Tax (GST per state)              │
   │   Affordability (EMI, Exchange, super.app Pay Later)               │
   └─────┬──────────────────────────────────────────────────────────────┘
         │
         ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       FULFILLMENT & LOGISTICS                        │
   │   WMS (warehouse)  •  eKart line-haul  •  Delivery hubs             │
   │   Kirana partner network  •  3PL couriers (Delhivery, Xpressbees)   │
   │   Returns / RTO engine  •  NDR (non-delivery) workflow              │
   └─────────────────────────────────────────────────────────────────────┘
```

### The mental model: **two planes, one event bus**

Think of Flipkart as **two planes glued by Kafka**:

```
   ┌──── COMMERCE PLANE ────┐         ┌──── FULFILLMENT PLANE ────┐
   │  search, cart,         │         │  WMS, eKart, delivery     │
   │  checkout, payment     │         │  hubs, returns            │
   └───────────┬────────────┘         └────────────┬──────────────┘
               │                                    │
               └──────────► KAFKA EVENT BUS ◄──────┘
                   (ORDER_CREATED, PAYMENT_SUCCESS,
                    PICK_DONE, SHIPPED, DELIVERED,
                    RETURN_INITIATED, RTO_TRIGGERED)
```

The commerce plane is **fast and synchronous** — checkout must answer in 1–2s. The fulfillment
plane is **slow and asynchronous** — a package physically travels for 1–5 days. Kafka is the
decoupling layer; a slow warehouse never blocks checkout.

### The Order State Machine

Every order walks a strict state machine. Only the **Order Service** is allowed to transition it,
and each transition is logged for audit + analytics.

```
   [CREATED] ──payment success──▶ [CONFIRMED] ──pick──▶ [PACKED]
        │                                                     │
        │ payment fail / UPI timeout                          │ ship
        ▼                                                     ▼
   [CANCELLED]                                          [SHIPPED]
                                                             │
                                                             │ out for delivery
                                                             ▼
   [RETURNED] ◀──return window── [DELIVERED] ◀──attempt── [OFD]
        │
        │ refund
        ▼
   [REFUNDED]
```

For COD orders the diagram has no "payment success" — instead the order moves to `CONFIRMED` on a
**risk-score pass** (a fraud model decides whether to ship on credit). Payment only happens (or
fails) at the door.

---

## 3. Detailed Component Breakdown

### 3.1 Catalog service

Owns product metadata: title, description, images, attributes (RAM, size, brand), category,
**HSN code** (for GST), brand store link. The catalog is sharded by `product_id` and read
heavily; writes come from seller self-service tools (Seller Hub) and the Flipkart merchandising
team.

Two important India-specific twists:

- **Multi-lingual catalog.** Titles/descriptions are localised into Hindi, Tamil, Telugu, etc.
  Translation can be on-the-fly (ML) or curated.
- **Affordability metadata.** Each product carries EMI eligibility, exchange eligibility, and
  "is this eligible for BBD offer X" flags. These feed the pricing engine.

### 3.2 Search & Ranking service

Flipkart search is **in-house** with Elasticsearch as a base, but the ranker is custom — trained
on Indian click/purchase data. Ranking signals include:

- Relevance (BM25 + semantic embeddings for Hindi/Hinglish queries)
- Sales velocity (how fast is it selling *this week*, weighted up during BBD)
- In-stock at the customer's pin code
- Seller rating + Flipkart-assured flag
- Affordability (EMI/Exchange available boosts rank for price-sensitive categories)
- Personalisation (the user's past category affinity)

```
   query: "redmi note 13 under 15000"
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │   SEARCH SERVICE                             │
   │  1. Query understanding (synonyms, Hindi)    │
   │  2. Recall: BM25 + vector (Hindi-aware)      │
   │  3. Filter: in-stock @ 452001, F-Assured     │
   │  4. Apply active BBD offers                  │
   │  5. Rank by GBDT (sales, CTR, affordability) │
   │  6. Return: product_ids + facets + sort keys │
   └──────────────────────────────────────────────┘
```

### 3.3 Pricing & Offers engine

The hardest "soft" problem. A product's displayed price depends on:

- Base list price (set by seller)
- Category-level BBD discount (merchandising)
- Brand-funded offer (e.g., 10% off on Samsung)
- **Bank offer** ("₹2,000 off on HDFC credit card") — bank-funded, bank-targeted
- Exchange offer (trade in old phone → ₹X off)
- No-cost EMI calculation (split across 3/6/9 months, interest subvented)
- super.app cashback
- Coupon applied at cart

This is a **price waterfall** computed at request time and cached for seconds-to-minutes. During
BBD the offers table mutates continuously; the cache TTL is short and the invalidation fan-out is
hot.

### 3.4 Cart & Checkout

Cart is a per-user, short-lived structure (Redis + persistent backup). Checkout is the
**pipeline**:

1. **Re-validate** every line item — price, in-stock, offer still valid.
2. **Reserve inventory** atomically (see 3.5).
3. **Affordability check** — EMI eligibility hits a credit-bureau (CIBIL) or internal score;
   exchange requires an appraisal quote.
4. **Payment routing**:
   - UPI → fire a collect request via the PSP; wait for callback (async!).
   - Card → PSP tokenisation + 2FA (OTP).
   - COD → fraud risk-score; if pass, confirm on credit.
   - BNPL / super.app Pay Later → internal ledger debit.
5. **GST + shipping** computation (state-dependent; GST is levied where the goods are consumed).
6. **Create order** in the Orders store; emit `ORDER_CREATED`.

### 3.5 Inventory service

Tracks stock per SKU per **fulfillment node** (FC, seller warehouse, dark store). Reservation is
atomic:

```
   UPDATE inventory
   SET reserved = reserved + 1
   WHERE sku=? AND node_id=? AND (on_hand - reserved) >= 1
   RETURNING reserved
```

If zero rows → out of stock at that node; try another node or fail. Inventory is **strongly
consistent within a node, eventually consistent across the network** — same pattern as Amazon.

### 3.6 Payment service

The most India-specific component. Integrates with:

- **PSPs**: Razorpay, Juspay, BillDesk, PhonePe PG, in-house PG.
- **UPI rails**: via PSP → NPCI switch → customer's PSP → customer bank. Async, callback-driven.
- **Card networks**: Visa, Mastercard, RuPay, Amex — with 2FA (OTP).
- **COD**: no charge; trust + reconciliation later.
- **BNPL**: super.app Pay Later, Simpl, Lazypay.
- **Wallets**: PhonePe, Paytm, Mobikwik.

Critical properties: **idempotency** (keyed by `checkout_id`), **auth-vs-capture** for prepaid
(capture at ship), **reconciliation** (match every order to a settlement file from each PSP), and
**graceful UPI timeout handling** (UPI collects can take 5–30s; the service holds the order in
`PAYMENT_PENDING` and lets the callback resolve it).

### 3.7 Affordability engine (EMI, Exchange, BNPL)

This is Flipkart's differentiator versus a vanilla Amazon clone. Three sub-systems:

- **EMI engine**: Given a product price + tenure + bank, compute the monthly EMI and the
  subvented interest. Calls the bank's EMI API.
- **Exchange engine**: Customer enters old device → ML model appraises it → quote generated →
  pickup scheduled by a separate logistics flow (Flashbucks / Yaantra).
- **BNPL / Pay Later**: Internal credit ledger; real-time underwriting from the customer's
  Flipkart history.

### 3.8 Order pipeline & workflow

After payment (or risk-pass for COD), the order enters a workflow engine that steps it through:
fraud re-check → GST invoice → warehouse dispatch → carrier handoff → tracking → delivery
confirmation → (for COD) cash collection reconciliation → (for prepaid) payment capture. Each step
emits events; downstream services react.

### 3.9 Fulfillment: eKart + WMS + last-mile

- **WMS** (warehouse management): pick path optimisation, bin-level tracking, packing
  instructions, GST invoice printing, manifest generation.
- **eKart** line-haul: trucks between FCs and sort centres and delivery hubs.
- **Last-mile**: Flipkart delivery executives + **kirana partners** (mom-and-pop stores act as
  pickup/drop points) + 3PL couriers (Delhivery, Xpressbees, Shadowfax) for tail coverage.
- **Returns / RTO**: Returns are *huge* in Indian e-commerce (especially fashion, ~30%). The RTO
  (Return-to-Origin) engine handles packages that bounce back at the door.

### 3.10 Recommendations & personalisation

"Customers who bought this also bought," "Frequently bought together," home-feed ranking — all
powered by offline batch jobs (item2vec, GBDT rankers) and served from a low-latency KV store
(ScyllaDB / Aerospike). Real-time signals (last 3 clicks) re-rank via a lightweight online model.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐  owns   ┌──────────────┐  lists   ┌──────────────┐
   │   Seller     │1───────*│   Product    │1────────*│   Listing    │
   │ - id         │         │ - id (FSN)   │         │ - seller_id  │
   │ - name       │         │ - title      │         │ - price      │
   │ - rating     │         │ - brand      │         │ - sku        │
   │ - GSTIN      │         │ - category   │         │ - condition  │
   └──────────────┘         │ - attributes │         └──────┬───────┘
                            │ - HSN code   │                │ has
                            └──────────────┘                ▼
                                                    ┌──────────────┐
                                                    │  Inventory   │
                                                    │ - sku        │
                                                    │ - node_id    │
                                                    │ - on_hand    │
                                                    │ - reserved   │
                                                    └──────────────┘

   ┌──────────────┐ places  ┌──────────────────────────────────────────┐
   │   Customer   │1───────*│   ORDER                                   │
   │ - id         │         │ - id, customer_id                         │
   │ - addresses  │         │ - items[]: {sku, qty, price, node}        │
   │ - UPI vpa    │         │ - status: CREATED→CONFIRMED→...→DELIVERED │
   │ - super.app? │         │ - payment_mode: UPI/CARD/COD/BNPL         │
   └──────────────┘         │ - shipping_addr, GST, total               │
                            └──────────────────────────────────────────┘
```

**Naming note**: Flipkart uses **FSN** (Flipkart Serial Number) for products, **listing_id** for a
seller's specific offer on a product, and **order_id** for the order. This three-level indirection
is the same as Amazon's ASIN → listing → order pattern.

### 4.2 Storage choices

| Data                            | Store                              | Why                                            |
| ------------------------------- | ---------------------------------- | ---------------------------------------------- |
| Product catalog                 | MySQL (sharded) + S3 (images)      | Relational attributes, faceted reads           |
| Search index                    | Elasticsearch (custom ranker)      | Full-text, ranking, faceted retrieval          |
| Cart                            | Redis (with MySQL backup)          | Per-user, low-latency, TTL                     |
| Inventory                       | MySQL + strongly consistent reads  | Atomic conditional decrements                  |
| Orders                          | MySQL (sharded) + event log (Kafka)| ACID + append-only audit                       |
| Payments                        | MySQL + PSP settlement files       | ACID, reconciliation                           |
| Recommendations                 | Aerospike / ScyllaDB               | Pre-computed, sub-ms reads                     |
| User sessions / OTP             | Redis                              | Short-lived, hot                               |
| Event bus                       | Kafka                              | Decouple commerce from fulfillment             |
| Geo (pin code serviceability)   | MySQL + Redis cache                | "Can we ship to 110001?" lookups               |
| Real-time feature store (ML)    | Online KV + Spark streaming        | Personalisation, fraud scoring                 |

### 4.3 Why MySQL (sharded) for catalog and orders

Flipkart is famously **MySQL-heavy** — unlike Amazon's DynamoDB preference. The reason is
historical + practical: relational integrity on the catalog, ACID transactions on orders and
payments, and a strong internal sharding platform (Flipkart built **DawnDB** and other sharding
layers on top of MySQL). The trade-off is more operational work, but the team has the muscle for
it.

### 4.4 Why Kafka is the spine

Every state change — inventory, price, order status, payment result, pick done, shipped,
delivered, return initiated — is published to Kafka. Dozens of consumers (search re-indexer,
recommendation feeder, analytics, fraud, notification, finance reconciliation) react
asynchronously. Checkout never blocks on them.

---

## 5. Request Flow — Placing a Flipkart Order During Big Billion Days

This is the canonical "design Flipkart" interview question. Let's walk a real BBD purchase: a
customer in Indore (pin 452001) buying an iPhone 15 at 12:00:03 AM on Day 1 of BBD, paying via UPI.

```
CUSTOMER      EDGE/CDN     SEARCH    CATALOG    INVENTORY   OFFERS      CHECKOUT   PAYMENT(UPI)
   │             │           │         │           │          │            │           │
   │─search────▶│           │         │           │          │            │           │
   │ "iphone 15" │           │         │           │          │            │           │
   │             │─route───▶│         │           │          │            │           │
   │             │           │─meta──▶│           │          │            │           │
   │             │           │◀───────┤           │          │            │           │
   │             │           │─stock@452001?──────▶│         │            │           │
   │             │           │◀─────counts─────────┤         │            │           │
   │             │           │─active BBD offers?────────────▶│           │           │
   │             │           │◀─────offer tags────────────────┤           │           │
   │             │           │  rank + return top N                       │           │
   │             │◀─results──┤            │           │          │            │           │
   │◀─search page┤           │            │           │          │            │           │
   │             │           │            │           │          │            │           │
   │─PDP click──▶│           │            │           │          │            │           │
   │             │─PDP fetch (catalog + price waterfall + EMI + reviews)              │
   │◀─PDP────────┤           │            │           │          │            │           │
   │             │           │            │           │          │            │           │
   │─Add to Cart▶│──────────────────────────────────────────────────────────▶│         │
   │             │           │            │           │          │  cart svc  │           │
   │             │           │            │           │          │            │           │
   │─Place Order▶│──────────────────────────────────────────────────────────▶│         │
   │             │           │            │           │          │            │           │
   │             │           │            │           │          │   [Checkout pipeline:] │
   │             │           │            │           │          │   1. validate cart      │
   │             │           │            │           │          │   2. RESERVE inventory ◀│
   │             │           │            │           │          │      (atomic decr)     │
   │             │           │            │           │          │   3. EMI/exchange check │
   │             │           │            │           │          │   4. GST compute        │
   │             │           │            │           │          │   5. UPI collect req ───▶│
   │             │           │            │           │          │            │           │
   │             │           │            │           │          │            │  ─PSP─▶NPCI─▶│
   │             │           │            │           │          │            │     bank   │
   │             │           │            │           │          │            │            │
   │             │           │            │           │          │   (order stays in PAYMENT_PENDING)
   │             │           │            │           │          │            │           │
   │◀─────"approve in UPI app" push───────────────────────────────────────────┤           │
   │             │           │            │           │          │            │           │
   │─approve in PhonePe────▶│            │           │          │            │           │
   │             │           │            │           │          │            │           │
   │             │           │            │           │          │   ◀──PAYMENT_SUCCESS callback───│
   │             │           │            │           │          │   6. order → CONFIRMED  │
   │             │           │            │           │          │   7. capture payment    │
   │◀────order confirmation + ETA─────────────────────────────────┤           │
   │             │           │            │           │          │            │           │
   │             │           │            │           │          │   [Event: ORDER_CREATED → Kafka]
   │             │           │            │           │          │      ▼                  │
   │             │           │            │           │          │   Fulfillment svc picks FC │
   │             │           │            │           │          │   (Bengaluru FC has stock + cheapest lane to 452001)
   │             │           │            │           │          │   WMS push: pick list     │
   │             │           │            │           │          │   eKart line-haul        │
   │             │           │            │           │          │   Last-mile to Indore hub │
   │◀────"Out for delivery" + tracking link (next morning)────────┤           │
```

**Step-by-step:**

1. **Search.** App hits the edge → search service. Query is parsed ("iphone 15"), matched against
   the Hindi/Hinglish-aware index, filtered to in-stock at pin 452001 and Flipkart-Assured
   listings, ranked by the GBDT ranker (BBD sales velocity weighted high), and the top 30 returned.
2. **PDP.** Customer taps a product. The PDP service fetches catalog metadata, computes the live
   price waterfall (list − BBD offer − bank offer − exchange estimate), computes EMI options,
   pulls review summaries, and returns a single aggregated payload.
3. **Add to cart.** Cart service writes `{customer_id, sku, qty, added_at}` to Redis with a TTL.
   No inventory reserved yet — carts are cheap; ~40% of carts never convert.
4. **Place Order.** The checkout pipeline runs synchronously:
   - **Validate**: re-fetch prices and stock (they can change in the 3 minutes since PDP).
   - **Reserve inventory**: atomic conditional decrement in the Inventory service, scoped to the
     FC chosen by the node-selector. This is the moment of truth — if 100 customers race for the
     last iPhone, only one wins; the others see "sold out, sorry."
   - **Affordability**: if EMI, check tenure eligibility with the bank; if exchange, generate an
     appraisal quote.
   - **GST + shipping**: compute state GST (Madhya Pradesh GST on the sale) + shipping lane cost.
   - **UPI collect**: the payment service fires a UPI collect request via the PSP. The order
     moves to `PAYMENT_PENDING`. **Crucially, the checkout returns here** — it does not block
     waiting for UPI; the customer is told "approve in your UPI app."
5. **Customer approves** in PhonePe → NPCI → PSP → Flipkart payment webhook.
6. **`PAYMENT_SUCCESS` callback**: the payment service verifies the signature, applies
   idempotency (so a duplicate webhook doesn't double-process), moves the order to `CONFIRMED`,
   and **captures** the payment.
7. **Order confirmation** returned to the customer with an ETA ("Delivery by Fri, 4 Nov").
8. **`ORDER_CREATED` event** published to Kafka.
9. **Fulfillment service** consumes the event, runs the **node-selector**: which FC has stock
   *and* the cheapest/fastest lane to 452001? Bengaluru FC wins. A pick list is pushed to the WMS.
10. **WMS** picks the unit from its bin, packs it with a printed GST tax invoice, prints an eKart
    shipping label, and manifests it onto a line-haul truck.
11. **eKart line-haul** moves the package Bengaluru → Indore sort centre → Indore delivery hub.
12. **Last-mile** delivery executive (or kirana partner) takes it out for delivery.
13. **`DELIVERED` event** → payment capture finalised if deferred; for COD, cash collection is
    reconciled against the order.

**The COD variant**: steps 5–6 are replaced by a fraud-risk check. If the customer's risk score
passes, the order moves `CREATED → CONFIRMED` immediately and ships on credit. The risk: ~25–30%
of COD orders RTO (bounce back), so the fraud model has to be good.

---

## 6. Scaling Strategy

### 6.1 Service decomposition + cell-based architecture

Flipkart is rumoured to run a **cell-based** architecture for the highest-traffic services
(search, PDP, cart, checkout): multiple independent "cells," each capable of serving the whole
traffic, behind a router. If one cell misbehaves, the router drains it. This gives blast-radius
isolation — a bad deploy can't take down all of BBD.

### 6.2 Caching at every layer

```
   App cache ──▶ CDN (Akamai + Flipkart CDN) ──▶ Redis ──▶ MySQL
```

- **CDN** absorbs the bulk of PDP and image traffic. During BBD, >90% of PDP renders are served
  from edge cache.
- **Redis** holds hot fragments: price waterfalls (TTL seconds), inventory counts (TTL very
  short), session, cart.
- **App-level caching**: the BFF caches aggregated screen payloads for a few seconds to coalesce
  burst traffic.

### 6.3 Read replicas + eventual consistency

Most reads (PDP, search, category browse) are eventually consistent. Writes (order create,
payment) are strongly consistent. The split is deliberate and per-workload.

### 6.4 Event-driven fan-out

Inventory changes, price changes, offer activations, and order status changes all publish to
Kafka. Dozens of consumers react asynchronously: search re-indexer, recommendation feeder,
analytics, fraud, finance reconciliation, notification. **Checkout never waits for them.**

### 6.5 Horizontal sharding of catalog and orders

MySQL is sharded by `customer_id` (for orders/cart/history) and by `product_id` (for catalog).
Flipkart's internal sharding platform handles re-sharding and hot-key mitigation.

### 6.6 Big Billion Days capacity

This is *the* Flipkart scaling story. BBD prep starts 6+ months ahead:

- **Load testing** at 10–20× projected peak using replayed production traffic.
- **Pre-warming** caches with hot product data; pre-provisioning Redis, ES, and DB capacity.
- **Feature freeze** 2 weeks before BBD — no risky deploys during the sale.
- **War room**: SRE + engineering on-call 24×7 during the sale; real-time dashboards on latency,
  error rate, payment success rate, conversion.
- **Bot defence**: BBD attracts scalper bots trying to corner iPhone stock. The WAF + bot-score
  service applies rate limits, CAPTCHAs, and queueing on suspicious traffic.
- **Queueing / virtual waiting room**: when traffic exceeds capacity, customers are placed in a
  fair queue (a token-bucket lobby) instead of seeing 500s.

### 6.7 Multi-datacentre in India

Flipkart runs out of multiple India data centres (historically Mumbai + Chennai regions on AWS
India, plus own DCs). Orders and payments are replicated across AZs/DCs for DR. The catalog is
multi-read; writes go to a primary.

### 6.8 Affordability scaling

The EMI engine and exchange engine are called *per checkout*. During BBD these can hit thousands
of QPS. They are scaled independently (stateless services) and their downstream bank APIs are
wrapped in circuit breakers + caches (a customer's EMI eligibility doesn't change every second).

---

## 7. Tech Stack

| Layer                       | Technology                                                    |
| --------------------------- | ------------------------------------------------------------- |
| Cloud                       | AWS (Mumbai + Hyderabad regions) + own DCs                    |
| Edge                        | Akamai + Flipkart CDN, AWS WAF / Shield Advanced, in-house bot defence |
| API gateway / BFF           | Custom JVM gateway + routing                                 |
| Languages                   | **Java** (heavy), Scala, **Kotlin**, Go, Python (ML)         |
| Frameworks                  | Spring Boot, Dropwizard, in-house JVM frameworks             |
| Databases                   | **MySQL (sharded)**, Aurora, DynamoDB-style KV (in-house)    |
| Search                      | Elasticsearch + custom ranker (GBDT + embeddings)             |
| Caching                     | Redis, Aerospike, Memcached                                  |
| Streaming                   | Apache Kafka                                                 |
| Queues / async              | Kafka topics, in-house job runners                          |
| Orchestration               | In-house workflow engine + Temporal-style sagas              |
| ML / recommendations        | Spark, TensorFlow / PyTorch, XGBoost, online feature store   |
| Container/runtime           | Kubernetes (EKS), VMs, in-house deploy platform              |
| Observability               | In-house monitoring (similar to Prometheus/Grafana), OpenTelemetry, ELK |
| Logistics                   | Custom WMS, eKart line-haul system, last-mile routing        |
| Payments                    | Razorpay, Juspay, PhonePe PG, BillDesk, in-house PG; UPI via NPCI |
| Mobile                      | Native Android (Kotlin/Java), iOS (Swift), React Native in places |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐    /search, /product/:id     ┌──────────────┐     ┌──────────────┐
   │  Browser   │◀────────────────────────────▶│  Node/Flask  │◀───▶│  Postgres    │
   │  / app     │                              │   backend    │     │  (catalog)   │
   │            │                              └──────┬───────┘     └──────────────┘
   │            │                                     │
   │            │                              ┌──────▼───────┐     ┌──────────────┐
   │            │                              │ Elasticsearch│◀───▶│  products    │
   │            │                              │  (search)    │     │  (seeded)    │
   │            │                              └──────────────┘     └──────────────┘
   │            │
   │            │    /cart, /checkout           ┌──────────────┐     ┌──────────────┐
   │            │◀─────────────────────────────▶│  Cart +      │◀───▶│  Postgres    │
   │            │                              │  Checkout    │     │  (orders,    │
   │            │                              └──────┬───────┘     │   inventory) │
   │            │                                     │             └──────────────┘
   │            │                                     ▼
   │            │                              ┌──────────────┐
   │            │                              │  UPI sandbox │
   │            │                              │  (Razorpay / │
   │            │                              │   Cashfree)  │
   │            │                              └──────────────┘
```

### 8.2 Step-by-step build

1. **Catalog.** Postgres table: `products(id, title, description, price, image_url, category,
   brand)`. Seed with sample electronics/fashion data.
2. **Search.** Spin up Elasticsearch (Docker) or use Postgres `tsvector` for small catalogs. Add a
   `/search?q=iphone` endpoint.
3. **PDP.** `/product/:id` reads Postgres (cache in Redis for 30s).
4. **Offers waterfall.** Add an `offers(product_id, type, value, active)` table. Compute the final
   price as `list_price − sum(active offers)`. Cache the result per `(product_id, customer_pin)`.
5. **Cart.** Redis hash `cart:{user_id}` → `{product_id: qty}`. Or Postgres `cart_items`.
6. **Inventory.** Postgres `inventory(product_id, node_id, on_hand, reserved)`. Reserve atomically:
   ```sql
   UPDATE inventory SET reserved = reserved + 1
   WHERE product_id=? AND node_id=? AND (on_hand - reserved) >= 1
   RETURNING reserved;
   ```
7. **Checkout pipeline.** Single endpoint:
   - Validate cart + prices.
   - Reserve inventory (atomic).
   - Compute GST + shipping.
   - Create a UPI collect via Razorpay/Cashfree test mode with an idempotency key.
   - Hold the order in `PAYMENT_PENDING`; expose a webhook for `payment.success`.
   - On webhook: verify signature, move order to `CONFIRMED`, capture.
   - On failure/timeout (5 min): release the inventory reservation, mark `CANCELLED`.
8. **COD path (bonus).** Add a `risk_score` field on the customer; if `risk_score < threshold`,
   allow `CONFIRMED` without payment. (In your demo, hardcode the threshold.)
9. **Order events.** Publish `ORDER_CREATED` to Redis pub/sub; a worker "ships" it (logs + sends
   an email).
10. **Frontend.** React or plain HTML. Show search results, PDP with offers, cart, UPI checkout.
11. **BBD simulation (fun).** Use `vegeta` or `k6` to hit `/search` and `/checkout` at 1000 RPS
    and watch your MySQL sweat. This is how you learn *why* Flipkart shards.

### 8.3 What you'll learn

- Why UPI is **asynchronous** and how that changes checkout (no blocking wait).
- Why inventory reservation is atomic and how SQL `RETURNING` solves the oversell race.
- How an idempotency key prevents double-charges on duplicate webhooks.
- Why GST is **state-dependent** (place of supply rules) and must be computed at checkout.
- Why COD needs a fraud model, not a payment auth.

### 8.4 Cost for a weekend build

- A $5 VPS + Postgres + Redis + Elasticsearch (Docker) + Razorpay test mode = essentially free.
- Real Flipkart spends billions on the *physical* fulfillment network (eKart FCs, last-mile), not
  the software.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered          | Why Flipkart chose it                                  |
| ----------------------------------------------- | ------------------------------- | ------------------------------------------------------ |
| **JVM microservices on MySQL (sharded)**        | DynamoDB-style NoSQL            | Relational integrity on catalog/orders; in-house sharding muscle |
| **Asynchronous UPI checkout (no blocking wait)**| Synchronous polling             | UPI can take 30s; blocking kills throughput + UX       |
| **COD with fraud-risk model**                   | Mandate prepay                  | Indian market demands COD; fraud model is the gate     |
| **Atomic inventory reservation**                | Soft reservation + reconcile    | Correctness: only one customer wins the last iPhone     |
| **Event-driven order pipeline**                 | One big distributed transaction | Decouples fast checkout from slow fulfillment          |
| **Cell-based architecture for hot services**    | Single shared fleet             | Blast-radius isolation; BBD resilience                  |
| **In-house search ranker (not vanilla ES)**     | Pure BM25                       | Indian language queries, BBD velocity weighting, personalisation |
| **eKart in-house + 3PL tail**                   | Fully outsourced logistics      | Control on core lanes; 3PL covers long-tail pin codes  |
| **Multi-datacentre in India**                   | Single region                   | DR + latency for a pan-India user base                 |
| **Affordability as a first-class service**      | Treat EMI as just payment       | EMI/Exchange is the conversion lever in India          |

### The deepest trade-off

**COD trust vs. RTO cost.** Flipkart *could* mandate prepaid-only and eliminate RTO entirely.
But that would gut conversion in Tier-2/3 India where customers don't trust online payment for
big-ticket items. The compromise: a **fraud-risk model** that ships on credit, accepting a 25–30%
RTO rate as the cost of market access. Every COD order is a bet — the model has to be good enough
that the *profit on good COD orders* exceeds *the loss on RTO'd ones*.

---

## 10. Common Interview Questions

**Q1: Design Flipkart / an e-commerce platform for India.**
Walk the customer journey (search → PDP → cart → checkout → fulfillment). Decompose into
services: catalog, search, pricing/offers, cart, checkout, inventory, payment (UPI/Card/COD),
order pipeline, fulfillment. Stress the India specifics: UPI async, COD + fraud, GST per state,
BBD traffic, multi-DC.

**Q2: Big Billion Days — your checkout is seeing 100k orders/sec. How do you scale?**
Three levers: (1) **stateless services auto-scale** behind a LB; (2) **caching** (CDN + Redis)
absorbs read bursts — only ~5% of traffic hits the DB; (3) **async payment** (don't block on UPI)
so checkout latency stays <2s; (4) **queueing** for overspill; (5) **pre-warmed** DB/Redis/ES
capacity; (6) **feature freeze + war room** during the sale.

**Q3: How do you prevent overselling when 1000 people click "Buy" on the last iPhone?**
Atomic conditional decrement in inventory. SQL `UPDATE ... WHERE on_hand - reserved >= 1 RETURNING`.
Only one wins; others fail and see "sold out." Inventory is strongly consistent within a node.

**Q4: UPI payment — how do you handle the async nature?**
At checkout, fire a UPI collect request via the PSP and **return immediately** with
`PAYMENT_PENDING`. Don't block. Expose a webhook; the PSP calls back on success/failure with a
signed payload. Verify signature, apply idempotency key, transition order. Set a 5-min timeout;
on timeout release inventory and cancel.

**Q5: How do you handle COD?**
No payment at checkout. Instead: a fraud-risk model scores the customer (history, RTO rate,
address quality). If pass, order is `CONFIRMED` on credit and shipped. Cash is collected at the
door; reconciliation matches collection to order. If customer refuses → RTO; the package is
returned, refund (for prepaid deposit) issued, and the customer's risk score degrades.

**Q6: How do you compute the price waterfall during BBD when offers change continuously?**
Compute at request time from base price + active offers + customer context (bank, exchange).
Cache per `(product_id, customer_pin)` with a short TTL (seconds). Offer changes publish to
Kafka → cache invalidation fan-out. Hot products get extra replica caches.

**Q7: Why MySQL sharded instead of DynamoDB?**
Relational integrity on catalog (faceted attributes, HSN/GST), ACID on orders and payments,
strong internal sharding platform. The trade-off is operational overhead vs. Amazon's managed
NoSQL.

**Q8: How does Flipkart handle returns at scale?**
Returns are first-class. A return request triggers a reverse-logistics flow: pickup scheduled,
item inspected at a returns hub, refund (or replacement) issued. The RTO/returns engine is
sized for ~30% return rates in fashion.

**Q9: How is search ranked for Indian queries?**
Lexical (BM25) + semantic embeddings trained on Hindi/Hinglish/Tamil/etc. click data, filtered by
in-stock@pincode + F-Assured, ranked by GBDT (sales velocity weighted high during BBD,
affordability available, personalisation). Results cached for seconds.

**Q10: Your payment service goes down during BBD. What happens?**
Circuit breaker trips; checkout fails fast with a retry message. Inventory reservations are
released after a TTL. No order is created. No double-charge because of idempotency keys. The
cell-based architecture means only one cell's worth of traffic is affected; the router drains it.

**Q11: How do you keep search fresh when inventory changes?**
Inventory changes publish to Kafka → a consumer updates the search index near-real-time. A
product out of stock disappears from search within minutes (during BBD, within seconds).

**Q12: Design the EMI / exchange offer engine.**
EMI engine: given (price, tenure, bank), call bank's EMI API or compute locally with subvented
interest; cache by `(price, tenure, bank)` for minutes. Exchange engine: customer describes old
device → ML appraisal model → quote; pickup scheduled by a separate logistics flow. Both are
stateless, independently scalable, circuit-broken against bank APIs.

---

## Further reading

- Flipkart tech blog (tech.flipkart.com / Medium @flipkart-tech) — engineering talks on BBD scale.
- "Scaling Flipkart during Big Billion Days" — conference talks (Roots, AWS re:Invent, Surge).
- Razorpay / Juspay engineering blogs — UPI payment flows.
- NPCI UPI specification — the underlying payment rail.
- Amazon Builders' Library — the analogous Amazon patterns Flipkart adapts.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures, engineering talks, and
analyst estimates.*

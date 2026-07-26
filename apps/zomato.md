# Zomato — System Design Atlas

> **One-line summary:** Zomato is a hyperlocal food-delivery marketplace that connects 50+ million
> monthly transacting users with hundreds of thousands of restaurants across ~1,000 Indian cities,
> dispatching a delivery partner to your door in ~30 minutes — a real-time three-sided marketplace
> (customer ↔ restaurant ↔ rider) powered by geospatial indexing, a live order state machine, and a
> heavy dose of ML for search ranking, dispatch, and demand forecasting.

---

## 1. Overview & Scale Numbers

Zomato looks simple on the surface: open app, pick restaurant, order food, eat. Underneath, it's a
**real-time three-sided marketplace with a 30-minute clock**. Unlike Flipkart (where the package
sits in a warehouse for hours), here the "package" is hot biryani — every second of delay degrades
the product. The interesting design tension is **matching supply and demand in space and time**.

### The numbers

| Metric                                        | Approximate value              | Why it matters                                            |
| --------------------------------------------- | ------------------------------ | -------------------------------------------------------- |
| Monthly transacting users                     | ~20M+ (India + UAE)            | Drives peak dinner concurrency                           |
| Restaurants on platform                       | ~250,000+                      | Each is a "warehouse" with limited capacity              |
| Cities active                                 | ~1,000                         | Hyperlocal — each city is many independent zones         |
| Delivery partners (riders)                    | ~300,000+ active               | Each emits GPS pings every 3–5s                          |
| Orders per day (peak)                         | ~3M+                           | Dinner peak dominates; ~60% of orders in a 3-hour window |
| Orders per second at peak                     | ~1,500+ (India dinner rush)    | Dispatch latency target: <30s to assign a rider          |
| Average delivery time                         | ~30 minutes                    | The product's core SLA                                   |
| GTV (gross transaction value)                 | ~₹15,000+ crore/quarter        | Drives payment + reconciliation scale                    |
| Gold / loyalty subscribers                    | ~5M+                           | Subscription = recurring revenue + retention             |
| IPL / festival peak                           | 2–3× normal dinner traffic     | Special-event capacity planning                          |
| Rider GPS pings per second                    | tens of thousands              | Drives the geospatial index architecture                 |
| Payment: UPI share                            | >70% of prepaid                | Async UPI flow with idempotency                          |
| COD share                                     | non-trivial in some cities     | Cash reconciliation at the door                          |

### The product goal

A customer in **Koramangala, Bengaluru** opens Zomato at 8:30 PM on a Saturday, searches "biryani,"
sees ranked restaurants within ~5 km in 200ms, picks one, adds items, checks out via UPI, and
within 30 minutes a rider hands them hot biryani. Behind the scenes: restaurant receives the order
in ~5s, accepts/rejects it, a rider is auto-assigned via a dispatch optimisation, the rider rides
to the restaurant, waits if needed, picks up, rides to the customer — all tracked live on the map.
Payment is reconciled, restaurant is settled, rider is paid.

### The analogy: it's Uber with three moving parts

If Uber is "match one rider to one driver," Zomato is **"match one customer to one restaurant AND
one rider, all within a 30-minute deadline, where the restaurant might be slow and the rider might
cancel."** Three independent actors, each with their own state, glued by an order state machine.

---

## 2. High-Level Architecture

```
   ┌───────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │   CUSTOMER APP    │  │  RESTAURANT APP /  │  │   RIDER APP        │
   │  - browse/search  │  │  TABLET (POS)      │  │  - receive orders  │
   │  - live tracking  │  │  - accept/reject   │  │  - broadcast GPS   │
   │  - pay (UPI/Card) │  │  - mark ready      │  │  - navigate        │
   └────────┬──────────┘  └─────────┬──────────┘  └──────────┬─────────┘
            │  HTTPS + WebSocket             │                        │
            │  (live tracking)                │                        │
            ▼                                 ▼                        ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       API GATEWAY / LOAD BALANCER                   │
   │                (TLS, auth, rate limit, BFF routing)                 │
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   ┌───────────┐           ┌──────────────┐        ┌──────────────┐
   │ Catalog / │           │  Search +    │        │  Restaurant  │
   │ Menu Svc  │           │  Ranking     │        │  Service     │
   │ (menus,   │           │  (ES + ML)   │        │  (CRUD, POS  │
   │  items)   │           │              │        │   webhook)   │
   └───────────┘           └──────────────┘        └──────────────┘
                                 │
   ┌─────────────────────────────▼──────────────────────────────────────┐
   │                       ORDER PLANE                                  │
   │   Order State Machine  •  Cart  •  Checkout  •  Payment           │
   │   (UPI/Card/COD/Wallet)  •  Pricing + Promotions + GST            │
   └─────────────────────────────┬──────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       DISPATCH CORE                                 │
   │   Geospatial Index (riders + restaurants)                           │
   │   Assignment Engine  •  ETA Service  •  Surge Pricing              │
   │   Rider Lifecycle  •  Live Tracking (WebSocket fan-out)            │
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       SETTLEMENT & FINANCE                          │
   │   Restaurant payouts  •  Rider payouts  •  Zomato commission       │
   │   TDS / GST reconciliation  •  Refunds                              │
   └─────────────────────────────────────────────────────────────────────┘
```

### The key abstraction: the Order State Machine

Every order is a state machine with strict transitions, driven by events from three different
actors (customer, restaurant, rider):

```
   [CREATED] ──restaurant accepts──▶ [ACCEPTED] ──rider assigned──▶ [RIDER_ASSIGNED]
                                                                            │
                                                                            │ rider reaches restaurant
                                                                            ▼
   [DELIVERED] ◀──rider delivers──── [PICKED_UP] ◀──food ready + rider picks up── [AT_RESTAURANT]
         │
         │ payment capture / COD reconcile
         ▼
   [COMPLETED]
```

Failure paths branch off at every step:
- Restaurant rejects → `CANCELLED`, refund.
- Restaurant doesn't accept in 60s → auto-cancel + refund.
- Rider cancels (twice) → re-dispatch, possibly with surge.
- Customer cancels before `ACCEPTED` → refund; after, partial refund.
- Delivery fails (customer unreachable) → `RETURNED_TO_RESTAURANT`.

### The key insight: dispatch is *predictive*, not reactive

The naïve dispatch ("wait for an order, then find the nearest free rider") is too slow. Zomato
**pre-positions riders** in high-demand zones using demand forecasts ("Saturday 8 PM in
Koramangala will need 500 riders"), and re-balances riders between zones as demand shifts. The
rider you get was probably already moving toward the restaurant before you ordered.

---

## 3. Detailed Component Breakdown

### 3.1 Catalog & Menu service

Restaurants own their menu: items, descriptions, images, prices, customisations (pizza toppings,
spice levels), veg/non-veg flags, **FSSAI licence**, and availability toggles. Menus are
versioned — a price change creates a new version; old orders reference the version they were placed
against (so a price change doesn't retroactively affect orders).

The menu service is sharded by `restaurant_id`; hot restaurants are cached in Redis. Availability
toggles ("item out of stock") are propagated to search in near-real-time.

### 3.2 Search & Ranking service

Zomato search is **ML-heavy**. Ranking signals:

- Relevance (text + dish embeddings — "paneer butter masala" should match even if the menu says
  "PBM")
- **Distance** (closer restaurants rank higher; the platform favours <5 km)
- Restaurant rating + rating count
- **ETA** (food arrives in 28 min → boost)
- **Live capacity** (if the restaurant's kitchen is jammed, suppress or mark "delivering later")
- Promoted placements (ads — labelled)
- Personalisation (the user's cuisine affinity, past orders)
- Gold / loyalty status

```
   query: "biryani near koramangala"
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │   SEARCH SERVICE                             │
   │  1. Query understanding (dish, cuisine)      │
   │  2. Geo-filter: restaurants within ~7 km     │
   │  3. Recall: BM25 + dish embeddings          │
   │  4. Apply live capacity + availability       │
   │  5. Rank by GBDT (rating, ETA, distance, ads)│
   │  6. Personalise                              │
   │  7. Return: restaurant cards + menus         │
   └──────────────────────────────────────────────┘
```

### 3.3 Restaurant service

- **CRUD** for restaurants (onboarding, menu management, photos, hours, FSSAI).
- **POS integration**: large chains (McDonald's, Domino's) have POS integrations so the order
  flows directly into their kitchen display system; cloud kitchens often use Zomato's own
  "Zomato Base" POS.
- **Webhook channel**: the restaurant app/tablet receives orders in ~5s, with an accept/reject
  button and a "mark ready" button.

### 3.4 Order service + state machine

The brain. Owns the order state machine; the only service allowed to transition order state.
Transitions are atomic, audited, and event-published. The service also owns **idempotency**
(duplicate checkout clicks → one order) and **timers** (auto-cancel if restaurant doesn't accept
in 60s).

### 3.5 Cart & Checkout

Cart is per-user, short-lived (Redis). Checkout is simpler than e-commerce (no shipping address
selection beyond the drop location), but with two India-specific concerns:

- **Payment**: UPI (async), Card, Wallet (Paytm, PhonePe, Amazon Pay), COD, and Zomato Gold /
  subscription discounts.
- **Promotions + Gold**: applied at cart; the engine validates coupons, Gold discounts, and
  campaign budgets (a "₹100 off" campaign has a daily budget cap).

### 3.6 Dispatch core — the heart of Zomato

This is the Uber-equivalent component, but harder. Three sub-systems:

#### 3.6.1 Geospatial index

Tracks the live location of every online rider, indexed by **geohash** or **S2 cell** for
"riders within X km of point P" queries. Updated on every GPS ping (every 3–5s per rider).

```
   ┌──────────────────────────────────────────────┐
   │   GEOSPATIAL INDEX (in-memory, sharded)      │
   │                                              │
   │   zone: koramangala_5km                      │
   │     rider_42: (lat, lng, last_seen, status)  │
   │     rider_88: (lat, lng, last_seen, status)  │
   │     ...                                      │
   │                                              │
   │   query: riders_near(lat, lng, radius)       │
   │     → returns candidates sorted by ETA       │
   └──────────────────────────────────────────────┘
```

#### 3.6.2 Assignment engine

Given a new order, the engine:
1. Computes the set of candidate riders (within ~3 km of the restaurant, free or about-to-be-free).
2. Ranks by **ETA to restaurant**, rider state, and rider's recent acceptance rate (don't assign
   to riders who keep rejecting).
3. **Auto-assigns** (the default) or sends a broadcast to top candidates who can accept/decline.
4. Handles re-dispatch if a rider cancels.

The auto-assign vs. broadcast decision is a key trade-off: auto-assign is faster but can feel
coercive to riders; broadcast gives riders agency but is slower.

#### 3.6.3 ETA service

Estimates travel time using **OSM / mapbox routing** + real-time traffic. ETA is computed for:
- Rider → restaurant (pickup ETA)
- Restaurant → customer (delivery ETA)
- Combined ETA = "food arrives in 32 min"

ETAs are re-computed live as the rider moves and the restaurant progresses; the customer's app
shows a live-updating ETA.

#### 3.6.4 Surge pricing

When demand outstrips rider supply in a zone, surge pricing kicks in. A "surge multiplier" (1.1x,
1.5x, etc.) is applied to delivery fee + menu prices. Surge is computed **per zone per time
window** from real-time demand/supply ratios; the goal is to *either* dampen demand (higher
prices → fewer orders) *or* attract more riders (higher pay → more riders log in). Surge is
shown to customers upfront.

### 3.7 Live tracking (WebSocket fan-out)

The customer's app shows the rider on a live map. This requires **pushing rider GPS updates to
the customer in near-real-time**. Architecture:

```
   Rider GPS ping (3–5s)
        │
        ▼
   ┌────────────────┐    publish     ┌─────────────────┐
   │  Rider Location │──────▶────────│   Kafka topic   │
   │  Ingest         │               │   (rider_gps)   │
   └────────────────┘               └────────┬────────┘
                                            │
                                            ▼
                                   ┌────────────────────┐
                                   │  Live Tracking Svc │
                                   │  (WebSocket server)│
                                   │  maintains a map:  │
                                   │   order_id →       │
                                   │   [customer ws]    │
                                   └─────────┬──────────┘
                                             │ push
                                             ▼
                                   Customer app (map updates)
```

The WebSocket server is horizontally scalable, with **sticky sessions** (a customer's connection
sticks to one server). When a GPS update arrives for rider R, the service looks up "which orders
is R currently fulfilling?" → pushes to those customers.

### 3.8 Pricing & Promotions engine

Computes the final cart price: base item prices + packaging fee + delivery fee + taxes (GST 5% on
food, 18% on delivery fee + platform fee) − promotions − Gold discount. Campaign budgets are
tracked to avoid overspend.

### 3.9 Settlement & finance

- **Restaurant payout**: order total − Zomato commission − taxes − promotions funded by restaurant.
  Payouts are periodic (daily/weekly) to the restaurant's bank account.
- **Rider payout**: per-order payout + distance + incentives; paid weekly.
- **Reconciliation**: every order must match a payment capture, a restaurant payout, and a rider
  payout. The finance team runs daily T+1 reconciliations.

### 3.10 ML / data platform

- **Demand forecasting**: predicts orders per zone per 15-min window for rider pre-positioning.
- **ETA model**: travel time prediction with traffic + weather.
- **Search ranking**: GBDT rankers + dish embeddings.
- **Fraud**: fake reviews, promo abuse, rider collusion.
- **Personalisation**: home feed ranking, "reorder" suggestions.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐ has    ┌──────────────┐ has    ┌──────────────┐
   │  Restaurant  │1──────*│   Menu       │1──────*│   MenuItem   │
   │ - id         │        │ - version    │        │ - id         │
   │ - name       │        │ - active     │        │ - name       │
   │ - location   │        └──────────────┘        │ - price      │
   │ - rating     │                                │ - veg?       │
   │ - FSSAI      │                                │ - available? │
   │ - hours      │                                └──────────────┘
   └──────────────┘

   ┌──────────────┐ places  ┌──────────────────────────────────────────┐
   │   Customer   │1───────*│   ORDER                                   │
   │ - id         │         │ - id, customer_id, restaurant_id          │
   │ - addresses  │         │ - items[]: {menu_item_id, qty, price}     │
   │ - payment    │         │ - status: CREATED→...→DELIVERED           │
   │   methods    │         │ - drop_location (lat, lng)                │
   └──────────────┘         │ - payment_mode: UPI/CARD/COD              │
                            │ - assigned_rider_id                       │
                            │ - total, taxes, promos                    │
                            └──────────────┬───────────────────────────┘
                                           │ assigned
                                           ▼
                                  ┌────────────────┐
                                  │   Rider        │
                                  │ - id           │
                                  │ - location     │
                                  │ - status:      │
                                  │   FREE/ON_ORDER│
                                  │ - vehicle type │
                                  └────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                              | Why                                            |
| ------------------------------- | ---------------------------------- | ---------------------------------------------- |
| Restaurant catalog / menus      | MySQL (sharded) + Redis cache      | Relational, versioned, read-heavy              |
| Search index                    | Elasticsearch + ML ranker          | Geo + full-text + ranking                      |
| Cart                            | Redis                              | Per-user, short-lived                          |
| Orders                          | MySQL (sharded) + Kafka event log  | ACID + append-only audit                       |
| Rider live locations            | **In-memory geospatial index** (Redis Geo + custom) | High write throughput, low-latency queries |
| Order state machine             | MySQL + Redis lock                 | Strong consistency on transitions              |
| Live tracking (WebSocket)       | WebSocket servers + Redis pub/sub  | Fan-out to customer apps                       |
| Payments                        | MySQL + PSP settlement files       | ACID, reconciliation                           |
| Promotions / campaigns          | MySQL + Redis (budget counters)    | Atomic budget decrement                        |
| Event bus                       | Kafka                              | Decouple order events                          |
| ML feature store                | Spark + online KV (Feast-like)     | Real-time features for ranking/dispatch        |

### 4.3 Why an in-memory geospatial index for riders?

Rider GPS updates arrive at **tens of thousands per second**. A disk-based store can't keep up
with both writes and "riders within X km" queries at low latency. The pattern: writes go to an
in-memory store (Redis GEO or a custom S2/geohash index), snapshots are periodically persisted to
MySQL/ClickHouse for analytics.

### 4.4 Why MySQL for orders?

Orders need ACID (a half-transitioned order is a bug), relational joins (order → items → menu →
restaurant), and audit. MySQL is sharded by `customer_id` for order history and by `restaurant_id`
for restaurant-facing queries. Cross-shard queries (rare) go through an aggregation service.

---

## 5. Request Flow — Ordering Food on Zomato

Let's walk the canonical flow: customer in Koramangala orders biryani at 8:30 PM on Saturday,
paying via UPI.

```
CUSTOMER    EDGE      SEARCH    RESTAURANT    CART     CHECKOUT    PAYMENT    ORDER      DISPATCH     RIDER APP   RESTAURANT POS
   │          │         │         │           │          │          │         │           │            │            │
   │─search──▶│         │         │           │          │          │         │           │            │            │
   │ "biryani" │         │         │           │          │          │         │           │            │            │
   │          │─route──▶│         │           │          │          │         │           │            │            │
   │          │         │─geo filter (koramangala + 5km)─▶│         │          │         │           │            │            │
   │          │         │◀─ranked list (rating+ETA+ads)──┤          │          │         │           │            │            │
   │◀─results─┤         │         │           │          │          │         │           │            │            │
   │          │         │         │           │          │          │         │           │            │            │
   │─open rest.▶│       │         │           │          │          │         │           │            │            │
   │          │─menu fetch────▶│──│           │          │          │         │           │            │            │
   │◀─menu────┤         │         │           │          │          │         │           │            │            │
   │          │         │         │           │          │          │         │           │            │            │
   │─add items──────────────────────────────▶│          │          │         │           │            │            │
   │          │         │         │           │ cart svc │          │         │           │            │            │
   │          │         │         │           │ (Redis)  │          │         │           │            │            │
   │          │         │         │           │          │          │         │           │            │            │
   │─checkout▶│         │         │           │          │          │         │           │            │            │
   │          │──────────────────────────────────────────▶│         │         │           │            │            │
   │          │         │         │           │          │          │         │           │            │            │
   │          │         │         │           │   [Checkout pipeline:]       │         │           │            │            │
   │          │         │         │           │   1. validate cart + prices  │         │           │            │            │
   │          │         │         │           │   2. apply promos + Gold     │         │           │            │            │
   │          │         │         │           │   3. compute GST + fees      │         │           │            │            │
   │          │         │         │           │   4. UPI collect request ────│─────────│────────▶│ (PSP→NPCI) │            │            │
   │          │         │         │           │   5. create ORDER (CREATED)──│─────────│────────▶│            │            │            │
   │          │         │         │           │   6. fire RESTAURANT_WEBHOOK ───────────────────────────────────────────────────▶│            │
   │          │         │         │           │          │          │         │           │            │  ─POS beeps─▶│            │
   │◀─────"pay in UPI app" + "order placed, waiting for restaurant"────────│          │         │           │            │            │            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │           │            │  ─restaurant taps ACCEPT─▶│            │
   │          │         │         │           │          │          │         │◀──ACCEPTED event────│───────────────────────────────────────────│
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │   [Event: ORDER_ACCEPTED → Kafka]
   │          │         │         │           │          │          │         │      ▼    │            │            │            │
   │          │         │         │           │          │          │         │   DISPATCH CORE:       │            │            │
   │          │         │         │           │          │          │         │   1. query riders within 3km of restaurant │            │            │
   │          │         │         │           │          │          │         │   2. rank by ETA + rider state           │            │            │
   │          │         │         │           │          │          │         │   3. auto-assign rider_42                │            │            │
   │          │         │         │           │          │          │         │   4. emit RIDER_ASSIGNED                │            │            │
   │          │         │         │           │          │          │         │      ▼    │            │            │            │
   │          │         │         │           │          │          │         │      └────│───────────│───────────▶│ (rider gets order)│            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │   [Event: PAYMENT_SUCCESS ← PSP webhook]   │            │            │
   │          │         │         │           │          │          │◀────────│───────────│            │            │            │
   │          │         │         │           │          │          │ capture │           │            │            │            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │   [Rider GPS pings every 3–5s → live tracking fan-out to customer]
   │◀──────live map updates (rider riding to restaurant)────────────────────────────────────────────────│───────────│────────────│────────────│
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │           │   ─rider arrives at restaurant─▶│            │
   │          │         │         │           │          │          │         │           │            │  ─food ready, rider taps PICKED_UP─▶│
   │          │         │         │           │          │          │         │◀──PICKED_UP event────────────────────────│◀───────────│            │
   │◀──────"rider has picked up your food"───────────────────────────────────────────────────│           │            │            │            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │           │   ─rider rides to customer──────▶│            │
   │◀──────live map updates───────────────────────────────────────────────────────────────────│           │            │            │            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │           │   ─rider arrives, taps DELIVERED──▶│           │
   │          │         │         │           │          │          │         │◀──DELIVERED event─────────────────────│◀───────────│            │
   │◀────"delivered!" + receipt───────────────────────────────────────────────────────────────│           │            │            │            │
   │          │         │         │           │          │          │         │           │            │            │            │
   │          │         │         │           │          │          │         │   [Settlement: restaurant payout, rider payout scheduled]
```

**Step-by-step:**

1. **Search.** Customer searches "biryani." Search service geo-filters to restaurants within ~5
   km, applies dish embeddings + restaurant ratings + ETAs + capacity, ranks via GBDT, returns
   cards. Latency <200ms.
2. **Menu fetch.** Customer opens a restaurant. Menu service returns the menu version, with
   availability flags and customisations.
3. **Add items.** Cart service writes to Redis. No inventory reserved — restaurants don't pre-reserve
   dishes (kitchen capacity is fluid).
4. **Checkout pipeline:**
   - **Validate** cart + re-fetch prices.
   - **Apply promotions** + Gold discount; decrement campaign budget atomically.
   - **Compute GST** (5% on food, 18% on fees).
   - **Fire UPI collect** via PSP → NPCI. Order state = `CREATED`, payment = `PENDING`.
   - **Emit `RESTAURANT_WEBHOOK`** to the POS/tablet.
5. **Restaurant receives** the order in ~5s. POS beeps, the staff taps ACCEPT (or auto-accept for
   trusted partners). `ACCEPTED` event.
6. **Dispatch core** consumes `ORDER_ACCEPTED`:
   - Queries riders within ~3 km of the restaurant.
   - Ranks by ETA, rider state, recent acceptance rate.
   - **Auto-assigns** rider_42; emits `RIDER_ASSIGNED`.
   - Rider app receives the order ("New order, pickup in 4 min").
7. **Payment callback** arrives: customer approved UPI in PhonePe → NPCI → PSP → Zomato webhook.
   Payment service verifies signature, applies idempotency, captures. Order payment finalised.
8. **Rider GPS pings** start flowing every 3–5s → live tracking service → WebSocket push to the
   customer's app. Customer sees the rider on the map.
9. **Rider arrives** at the restaurant. Waits if the food isn't ready.
10. **Food ready** + rider picks up → `PICKED_UP` event → ETA updates to delivery time.
11. **Rider rides** to the customer. Live tracking continues.
12. **Rider arrives** at the customer's drop location, hands over the food, taps DELIVERED.
    `DELIVERED` event.
13. **For COD**: cash collected at the door; reconciled later against the order.
14. **Settlement**: restaurant payout (order total − commission − taxes), rider payout (per-order +
    distance), scheduled in the finance system.

---

## 6. Scaling Strategy

### 6.1 Hyperlocal zone sharding

Zomato is fundamentally **per-city, per-zone**. Each zone (e.g., Koramangala) is a
mostly-independent operational unit. Dispatch, rider geospatial index, and demand forecasting are
sharded by zone. This gives natural horizontal scaling — adding a city is "stand up a new shard."

```
   Bengaluru shard                Hyderabad shard
   ├── Koramangala zone           ├── Hitech City zone
   ├── Indiranagar zone           ├── Banjara Hills zone
   ├── Whitefield zone            └── ...
   └── ...
```

### 6.2 Read-heavy caching

```
   App cache ──▶ CDN ──▶ Redis ──▶ MySQL
```

- Menus, restaurant metadata, and images are CDN-cached.
- Hot fragments (home feed, restaurant cards) cached in Redis for seconds-to-minutes.
- Live data (rider location, ETA, order status) bypasses cache and goes through the live
  tracking / order service.

### 6.3 WebSocket fan-out scaling

Live tracking is the hot path. WebSocket servers are horizontally scaled; sticky sessions route a
customer's connection to one server. Redis pub/sub broadcasts GPS updates to the right server
which pushes to the right customer. Scaled to millions of concurrent connections during dinner
peak.

### 6.4 Event-driven dispatch

Every order transition emits a Kafka event. Dispatch, settlement, analytics, notification, fraud
all consume asynchronously. The order service never blocks on them.

### 6.5 IPL / festival / New Year peak capacity

Special events cause **2–3× normal dinner traffic** in a zone. Zomato pre-positions extra riders
based on demand forecasts, pre-warms caches, and runs feature freezes during IPL finals / New
Year's Eve.

### 6.6 Predictive dispatch

The biggest scaling lever: **don't wait for orders to find riders — pre-position riders where
orders will appear.** Demand forecasts per zone per 15-min window drive rider deployment. Without
this, dinner peak would collapse into 60-minute deliveries.

### 6.7 Multi-region cloud

Zomato runs on AWS + GCP across India regions. Services are stateless and auto-scale; the
geospatial index and WebSocket servers are sharded per zone.

---

## 7. Tech Stack

| Layer                       | Technology                                                    |
| --------------------------- | ------------------------------------------------------------- |
| Cloud                       | AWS + GCP (India regions)                                    |
| Edge                        | CDN, WAF, bot defence                                         |
| API gateway / BFF           | Custom JVM gateway + routing                                 |
| Languages                   | **Python** (heavy), Go, Java, Kotlin, Swift                  |
| Frameworks                  | Django, FastAPI, gRPC, in-house Go services                  |
| Databases                   | MySQL (sharded), Redis (Geo), Aerospike                      |
| Search                      | Elasticsearch + custom ML ranker                              |
| Caching                     | Redis, Memcached                                             |
| Streaming                   | Apache Kafka                                                 |
| Geospatial                  | Redis GEO, S2 cells, custom indices                         |
| WebSocket                   | Custom WebSocket layer + Redis pub/sub                       |
| ML / data                   | Spark, TensorFlow / PyTorch, XGBoost, Feast-style feature store |
| Maps / routing              | Mapbox, OpenStreetMap, Google Maps                           |
| Container/runtime           | Kubernetes                                                    |
| Observability               | Prometheus, Grafana, ELK, OpenTelemetry                      |
| Payments                    | Razorpay, Juspay, PhonePe PG, in-house; UPI via NPCI         |
| Mobile                      | Native Android (Kotlin/Java), iOS (Swift)                    |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐  /search, /restaurant/:id    ┌──────────────┐     ┌──────────────┐
   │  Customer  │◀────────────────────────────▶│  Flask/Node  │◀───▶│  Postgres    │
   │  app       │                              │   backend    │     │ (restaurants,│
   │            │                              └──────┬───────┘     │  menus)      │
   │            │                                     │             └──────────────┘
   │            │                              ┌──────▼───────┐     ┌──────────────┐
   │            │                              │ Redis GEO    │     │  Postgres    │
   │            │                              │ (rider locs) │     │  (orders)    │
   │            │                              └──────────────┘     └──────────────┘
   │            │
   │            │  /order, /track (WebSocket)  ┌──────────────┐
   │            │◀────────────────────────────▶│  Order +     │
   │            │                              │  Dispatch    │
   │            │                              │  service     │
   │            │                              └──────┬───────┘
   │            │                                     │
   │            │                              ┌──────▼───────┐
   │            │                              │  Rider app   │
   │            │                              │  (simulator) │
   │            │                              └──────────────┘
```

### 8.2 Step-by-step build

1. **Restaurants + menus.** Postgres: `restaurants(id, name, lat, lng, rating)`,
   `menu_items(id, restaurant_id, name, price, veg)`. Seed with ~10 fake restaurants in your city.
2. **Search.** Use Postgres + `ST_Distance` for "restaurants within 5 km." For dish search, use
   Postgres full-text or a tiny embedding index.
3. **Cart.** Redis hash `cart:{user_id}` → `{item_id: qty}`.
4. **Order service.** A single `/order` endpoint that:
   - Validates cart + prices.
   - Creates an order row, status = `CREATED`.
   - Fires a webhook to the "restaurant" (in your demo, a script that auto-accepts after 5s).
   - Triggers dispatch.
5. **Rider simulator.** Write a Python script that:
   - Picks a random location.
   - Polls for assigned orders.
   - "Moves" toward the restaurant (updates lat/lng every 2s).
   - "Picks up," "moves" toward the customer, "delivers."
6. **Dispatch.** Use **Redis GEO**: `GEOADD riders <lng> <lat> rider_id`, and
   `GEORADIUS riders <lng> <lat> 3 km COUNT 5` to find nearby riders. Auto-assign the nearest.
7. **Live tracking.** WebSocket server (Flask-SocketIO or Node ws). Rider pings its location every
   2s → server pushes to the customer's WebSocket.
8. **Payment (skip UPI complexity).** Use Stripe test mode or mock the payment.
9. **Frontend.** React with a map (Leaflet + OpenStreetMap) showing the rider moving. A "Place
   order" button. A WebSocket-driven tracker.
10. **Have fun.** Watch the rider dot move from restaurant to customer. That's Zomato in miniature.

### 8.3 What you'll learn

- How **geospatial search** differs from keyword search (proximity matters).
- How a **state machine** orchestrates three independent actors.
- Why **WebSocket fan-out** is needed for live tracking.
- Why dispatch is **predictive** (pre-positioning) not reactive.

### 8.4 Cost for a weekend build

- A laptop running Postgres + Redis + Flask + Leaflet = free.
- Add a $5 VPS if you want to test from your phone.
- Real Zomato's costs are dominated by **rider payouts** (₹30–50 per order) and **customer
  acquisition** (ads, promos, Gold subsidy).

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered          | Why Zomato chose it                                  |
| ----------------------------------------------- | ------------------------------- | ---------------------------------------------------- |
| **Auto-assign dispatch (mostly)**               | Broadcast to all nearby riders  | Faster (~30s vs minutes); better UX; throughput      |
| **Pre-positioned riders (predictive)**          | Pure reactive dispatch          | Without it, dinner peak would collapse               |
| **In-memory geospatial index**                  | DB-backed location queries      | Tens of thousands of GPS pings/sec; sub-ms needed    |
| **WebSocket live tracking**                     | Polling                         | Real-time UX; lower bandwidth than polling           |
| **Hyperlocal zone sharding**                    | Single national shard           | Each city/zone is operationally independent          |
| **MySQL for orders + Kafka log**                | NoSQL                           | ACID + relational + audit                            |
| **Surge pricing**                               | Fixed pricing                   | Balances supply/demand at peak                       |
| **Gold subscription**                           | Pure per-order monetisation     | Recurring revenue + retention + data                 |
| **Async UPI payment**                           | Block on payment                | UPI can take 30s; blocking kills throughput          |
| **COD with reconciliation**                     | Mandate prepaid                 | Indian market demands COD                            |

### The deepest trade-off

**Throughput vs. rider autonomy in dispatch.** Auto-assign maximises throughput and speed — every
order gets a rider in ~30s, which is great for the customer. But it removes rider agency; riders
can feel coerced into accepting low-payout long-distance orders. Broadcast (let riders choose)
is fairer but slower and leaves some orders unassigned. Zomato leans heavily toward auto-assign
with guardrails (acceptance rate thresholds, minimum payout guarantees), accepting some rider
dissatisfaction as the cost of throughput.

---

## 10. Common Interview Questions

**Q1: Design Zomato.**
Walk the three-actor flow (customer → restaurant → rider). Decompose: catalog/menu, search, order
state machine, dispatch (geospatial index + assignment engine + ETA + surge), live tracking,
settlement. Stress the 30-minute SLA and predictive dispatch.

**Q2: How do you dispatch a rider to an order?**
Query riders within ~3 km from the restaurant using a geospatial index (Redis GEO / S2 / geohash).
Rank by ETA to restaurant + rider state + recent acceptance rate. Auto-assign the top candidate.
If they reject, fall back to the next candidate or broadcast. Use predictive pre-positioning so
riders are already near demand.

**Q3: How do you show the rider live on the customer's map?**
Rider app sends GPS pings every 3–5s → Kafka topic → live-tracking service maintains
`order_id → customer WebSocket` map → pushes location updates to the customer app via WebSocket.
Sticky sessions on the WS server for scalability.

**Q4: How do you handle dinner peak (1500 orders/sec)?**
Stateless services auto-scale. **Predictive dispatch** pre-positions riders per demand forecast.
Caching absorbs reads. Zone-sharded geospatial index. WebSocket layer scales horizontally. Feature
freeze + war room during IPL / New Year.

**Q5: How do you compute ETA?**
Routing engine (OSM / mapbox) for travel time + traffic. Plus restaurant prep-time estimate
(historical + current capacity). Combined ETA shown to customer; re-computed live.

**Q6: What happens if the restaurant rejects or doesn't accept?**
60s timer auto-cancels if no accept. Refund initiated. Customer is notified with alternative
suggestions. For prepaid, refund within minutes (Razorpay instant refund).

**Q7: How does surge pricing work?**
Per zone per time window, compute demand/supply ratio. If >1, apply surge multiplier (1.1x–2x) to
delivery fee + menu prices. Surge is shown upfront. Goal: dampen demand OR attract more riders
(higher pay).

**Q8: How do you handle UPI payment failures?**
Order stays in `PENDING` for up to 5 min. PSP webhook on success/failure. On failure, retry or
cancel. On timeout, release and cancel. Idempotency key on the checkout prevents double-charges.

**Q9: How do you store millions of rider locations?**
In-memory geospatial index (Redis GEO or custom S2). Writes at tens of thousands/sec; queries
sub-ms. Snapshots persisted to ClickHouse / MySQL for analytics.

**Q10: How do you settle restaurants and riders?**
Order completion triggers settlement entries. Restaurant payout = order total − commission −
taxes − restaurant-funded promos. Rider payout = per-order + distance + incentives. Payouts run
in periodic batches (daily/weekly) to bank accounts via NPCI / NEFT. Daily T+1 reconciliation
matches every order to its payouts.

**Q11: How do you rank restaurants in search?**
Geo-filter first (within ~5 km), then GBDT ranker on: rating, rating count, ETA, distance, live
capacity, personalisation, ads. Dish-level search uses dish embeddings for semantic match.

**Q12: How do you prevent promo abuse?**
Campaign budgets decremented atomically (Redis). Per-user coupon limits enforced at checkout.
Fraud model scores unusual patterns (same device, many accounts). Promo code rotation.

---

## Further reading

- Zomato engineering blog (zoma.to/engineering) — talks on dispatch, scale, ML.
- "Designing Uber" references (Uber's engineering blog) — the analogous dispatch problem.
- Redis GEO / S2 geometry documentation — for geospatial indexing.
- NPCI UPI specs — for the payment rail.
- Mapbox / OSM routing docs — for ETA.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures, quarterly reports, and
engineering talks.*

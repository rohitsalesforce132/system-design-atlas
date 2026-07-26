# Swiggy — System Design Atlas

> **One-line summary:** Swiggy is India's largest on-demand hyperlocal delivery platform — food
> delivery, quick-commerce grocery (Instamart, 10-min delivery), and Genie (pick-up-and-drop) —
> running a real-time three-sided marketplace (customer ↔ store/restaurant ↔ rider) across 500+
> cities with a fleet of 400,000+ riders, predictive dispatch, and an aggressively decentralised
> **dark-store** network that puts inventory within 1.5 km of most urban customers.

---

## 1. Overview & Scale Numbers

Swiggy looks like Zomato on the surface (food delivery), but its architecture is meaningfully
different in three ways:

1. **Quick-commerce (Instamart)** — the headline product. **10-minute grocery delivery** is not a
   marketing gimmick; it's a genuine systems challenge. You can't deliver in 10 min if the
   inventory is in a central warehouse 20 km away. So Swiggy puts **dark stores** within ~1.5 km
   of dense urban clusters, pre-picks popular SKUs, and optimises the *entire* pipeline (pick
   time, rider assignment, ride) to sub-10-min budgets.
2. **Multi-vertical on one fleet.** Swiggy runs food delivery, Instamart, and Genie on a *shared*
   rider pool. A rider might do a food delivery, then a 10-min Instamart run, then a Genie
   pickup. This is a harder dispatch problem than Zomato's (largely) single-vertical fleet.
3. **Owns the demand prediction layer end-to-end.** Swiggy is unusually public about its
   investment in **ML-driven dispatch, demand forecasting, and dark-store siting** — these are
   load-bearing, not optional.

### The numbers

| Metric                                        | Approximate value              | Why it matters                                            |
| --------------------------------------------- | ------------------------------ | -------------------------------------------------------- |
| Monthly transacting users                     | ~20M+                          | Dinner peak + quick-commerce interleaved                 |
| Restaurants on platform (food)                | ~250,000+                      | Same scale as Zomato                                     |
| Cities active                                 | ~500+ (food) / ~25+ (Instamart)| Instamart is metro/urban-first                           |
| Delivery partners (riders)                    | ~400,000+ active               | Shared across food + Instamart + Genie                   |
| Instamart dark stores                         | ~1,000+ (and growing)          | Each is a 2,000–6,000 sq ft micro-warehouse              |
| Instamart SKUs per dark store                 | ~4,000–6,000                   | Curated for the local cluster                            |
| Orders per day (food + Instamart + Genie)     | ~3M+                           | Spiked by quick-commerce growth                          |
| Orders per second at peak                     | ~2,000+ (dinner + Instamart)   | Dispatch latency target: <30s assign, <10 min delivery (Instamart) |
| Instamart target delivery time                | **~10 minutes**                | The defining SLA of quick-commerce                       |
| Instamart budget: store-to-customer ride      | ~4 minutes                     | Drives dark-store siting within 1.5 km                   |
| IPL / festival / weekend peak                 | 2–4× normal                    | Special-event capacity                                   |
| Rider GPS pings per second                    | tens of thousands              | Same geospatial index problem as Zomato/Uber             |
| Genie (pick-drop) share                       | growing                        | Unlocks low-density zones; utilises off-peak rider time  |
| Average rider trips per day                   | ~20–30                         | Multi-vertical batching matters                          |

### The product goal

A customer in **HSR Layout, Bengaluru** opens Swiggy Instamart at 9 PM on a Tuesday, searches
"curd, milk, eggs, bread," sees in-stock items in 200ms, adds to cart, checks out via UPI, and
within 10 minutes a rider hands them groceries. Behind the scenes: the order routes to the
nearest dark store (1.2 km away), a **picker** in the store gets a pick list on a handheld, picks
in ~2 min, a rider was already pre-positioned outside the store (or auto-assigned within 30s),
the rider rides 4 min, delivers. Total: pick (2) + assign (0.5) + ride (4) + handover (1) =
~7.5 min, under the 10-min budget.

For food delivery, the flow is the same as Zomato (search → restaurant → rider → 30-min delivery)
but with Swiggy's dispatch and a shared rider fleet.

### The analogy: it's Zomato + a chain of vending machines the size of apartments

Quick-commerce is the interesting twist. The **dark store** is the key abstraction — it's a small,
**customer-invisible warehouse** (you can't walk into it) positioned within ~1.5 km of dense
demand, stocked with the 4,000–6,000 SKUs most likely to be ordered from that cluster. The whole
architecture is designed around making the *store-to-customer* leg as short as possible.

---

## 2. High-Level Architecture

```
   ┌───────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │  CUSTOMER APP     │  │  PARTNER APP       │  │   RIDER APP        │
   │  (Food / Instamart│  │  (Restaurant /     │  │  - multi-vertical  │
   │   / Genie)        │  │   Dark store /     │  │  - GPS broadcast   │
   │                   │  │   Cloud kitchen)   │  │  - accept/navigate │
   └────────┬──────────┘  └─────────┬──────────┘  └──────────┬─────────┘
            │  HTTPS + WebSocket              │                        │
            ▼                                 ▼                        ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       API GATEWAY / BFF                             │
   │        (TLS, auth, rate limit, vertical-aware routing)             │
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
   ┌──────────────┐       ┌──────────────┐         ┌──────────────────┐
   │ Food Catalog │       │ Instamart    │         │  Search + Rank   │
   │  & Menus     │       │ Catalog      │         │  (per-vertical)  │
   │ (per restaurant)│    │ (per dark    │         │                  │
   │               │      │  store)      │         │                  │
   └──────────────┘       └──────────────┘         └──────────────────┘
                                 │
   ┌─────────────────────────────▼──────────────────────────────────────┐
   │                       ORDER PLANE                                  │
   │   Order State Machine  •  Cart  •  Checkout  •  Payment           │
   │   (UPI/Card/COD/Wallet)  •  Pricing + Surge + Promos              │
   │   Per-vertical pipelines: FOOD / INSTAMART / GENIE                 │
   └─────────────────────────────┬──────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       DISPATCH CORE                                 │
   │   Geospatial Index (riders + stores + restaurants)                  │
   │   Multi-vertical Assignment Engine (food + instamart + genie)       │
   │   ETA Service  •  Surge Pricing  •  Demand Forecast                 │
   │   Live Tracking (WebSocket fan-out)  •  Dark-store pick orchestration│
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       PHYSICAL NETWORK                             │
   │   Dark stores (Instamart)  •  Restaurant kitchens (food)           │
   │   Cloud kitchens (The Bowl Company)  •  Rider fleet                │
   │   Last-mile delivery hubs                                           │
   └─────────────────────────────────────────────────────────────────────┘
```

### The key insight: Instamart inverts the timing

For food delivery, the bottleneck is **restaurant prep time** (10–20 min). Dispatch can wait —
the rider has time to arrive while the food cooks.

For Instamart, the bottleneck is **store-to-customer ride time** — items are already on shelves.
So Swiggy inverts the order of operations:

```
   FOOD DELIVERY (timing):
     order → assign rider → rider rides (5 min) → food cooks (15 min) → pickup → ride (5 min) → deliver
     [rider waits for food; total ~30 min]

   INSTAMART (timing):
     order → picker picks (2 min) → rider rides (4 min) → deliver
     [rider is PRE-POSITIONED; total ~10 min]
```

For Instamart, **riders are pre-positioned at the dark store**, not floating in the city. The
dispatch assignment can happen *before* the pick is done, in parallel.

### The Order State Machine

Same general shape as Zomato, with an Instamart variant:

```
   FOOD ORDER:
   [CREATED] → [RESTAURANT_ACCEPTED] → [RIDER_ASSIGNED] → [AT_RESTAURANT]
      → [PICKED_UP] → [OFD] → [DELIVERED]

   INSTAMART ORDER:
   [CREATED] → [DARK_STORE_RECEIVED] → [PICKING] → [RIDER_ASSIGNED] (parallel)
      → [PICKED_UP] → [OFD] → [DELIVERED]   [total target: <10 min]
```

The Instamart state machine has tighter timers: if a pick takes >2 min or a ride takes >5 min, an
alert fires.

---

## 3. Detailed Component Breakdown

### 3.1 Catalog & inventory (two flavours)

- **Food catalog**: per-restaurant menus, like Zomato. Read-heavy, cached.
- **Instamart catalog**: per-dark-store inventory. Each dark store has its own count of each SKU.
  Inventory is **strongly consistent within a dark store** (so two customers don't order the last
  carton of milk) but eventually consistent across stores. This is the **e-commerce inventory
  problem** reborn at hyperlocal scale — see `flipkart.md` §3.5 and `amazon.md` §3.4.

```
   UPDATE dark_store_inventory
   SET reserved = reserved + 1
   WHERE store_id=? AND sku=? AND (on_hand - reserved) >= 1
   RETURNING reserved
```

### 3.2 Search & Ranking

Per-vertical rankers:

- **Food search**: dish embeddings + restaurant ratings + ETA + distance + ads + personalisation
  (like Zomato).
- **Instamart search**: SKU match + in-stock at nearest dark store + ETA (target <10 min) +
  promotions + "frequently bought together" bundles. Instamart search also surfaces **substitutes**
  ("out of stock — try this similar brand") because grocery substitution is common.

```
   query: "milk, bread, eggs"
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │   INSTAMART SEARCH                           │
   │  1. Resolve query → SKUs (incl. Hindi/local) │
   │  2. Check stock at nearest dark store        │
   │  3. Rank by popularity, promo, personalisation│
   │  4. Suggest substitutes for OOS items        │
   │  5. Return: SKU cards + per-store ETA        │
   └──────────────────────────────────────────────┘
```

### 3.3 Dark store management

A dark store is a small-format warehouse run like a mini-FC:

- **Inventory**: 4,000–6,000 SKUs, restocked daily from a "mother warehouse" via line-haul.
- **Pickers**: 3–6 staff with handheld devices; pick lists are sequenced by store layout for
  shortest walking path.
- **Packing**: items bagged, sealed, handed to rider.
- **Capacity model**: each store has a max concurrent-orders capacity; if exceeded, ETA slips or
  the store stops accepting orders ("high demand — try later").

The dark-store siting decision is itself an ML problem: where to open the next store to maximise
demand coverage within a 1.5 km radius.

### 3.4 Order service + state machine

Owns the per-vertical state machines. Key properties:
- **Idempotency** (duplicate checkout → one order).
- **Timers**: food restaurant must accept in 60s; Instamart pick must start in 30s; both have
  auto-cancel fallbacks.
- **Parallel dispatch for Instamart**: rider assignment starts *concurrently* with picking.

### 3.5 Cart & Checkout

Cart is per-user (Redis). Checkout:
1. Validate cart + prices.
2. For Instamart: **reserve inventory** atomically at the chosen dark store (this is the
   e-commerce reservation step — different from food, where there's no pre-reservation).
3. Apply promotions (Swiggy One membership, coupons, campaign budgets).
4. Compute fees (delivery fee + handling + surge + GST 18% on fees).
5. Payment: UPI (async) / Card / Wallet / COD.
6. Create order; emit events.

### 3.6 Dispatch core — multi-vertical

This is Swiggy's hardest component. Unlike Zomato (single vertical), Swiggy's dispatch must:

1. **Pool riders across verticals.** A rider finishing a food delivery at 9:05 PM might be the
   best candidate for an Instamart pickup at 9:06 PM nearby.
2. **Reserve riders at dark stores** for Instamart. Some riders are "parked" at Instamart dark
   stores during peak hours to guarantee sub-10-min pickup.
3. **Predict demand** to pre-position the right riders in the right zones for the right vertical.
4. **Honour vertical priority.** Instamart's 10-min SLA is tighter than food's 30-min; the
   assignment engine weights Instamart orders higher when both compete for a rider.

```
   ┌──────────────────────────────────────────────┐
   │   MULTI-VERTICAL ASSIGNMENT ENGINE           │
   │                                              │
   │   Inputs:                                    │
   │     - incoming orders (food + instamart +    │
   │       genie), each with SLA + ETA target     │
   │     - rider pool: location, current state,   │
   │       vertical eligibility                   │
   │                                              │
   │   Strategy:                                  │
   │     1. For each order, find candidate riders │
   │     2. Compute match score (ETA, slack vs    │
   │        SLA, rider acceptance rate, payout)   │
   │     3. Solve assignment (greedy or Hungarian)│
   │     4. Auto-assign or broadcast              │
   │     5. Pre-position riders for next 15 min   │
   └──────────────────────────────────────────────┘
```

The assignment is essentially a **bipartite matching problem** (orders ↔ riders) solved every few
seconds per zone. Greedy heuristics work at scale; more sophisticated solvers (Hungarian
algorithm, min-cost flow) give marginal gains.

### 3.7 ETA service

Travel-time estimates using routing engines + real-time traffic. For Instamart, ETA is critical —
the displayed "8 min" promise is computed from dark-store location + customer location + traffic
+ pick time. ETAs are re-computed live.

### 3.8 Surge pricing

Per-zone per-time-window surge multipliers. Swiggy also applies **category-level surge** (rain →
all Instamart fees up) and **item-level demand pricing** in extreme cases.

### 3.9 Live tracking (WebSocket fan-out)

Same as Zomato: rider GPS → Kafka → live-tracking service → WebSocket push to customer. Scaled to
millions of concurrent connections.

### 3.10 Pricing & Promotions / Swiggy One

- **Swiggy One**: subscription for free delivery + no surge + selected Instamart benefits.
- **Promotions**: coupons, cashback, campaign budgets. Tracked atomically.
- **Bundles**: "frequently bought together" pricing on Instamart.

### 3.11 ML / data platform

- **Demand forecasting**: per zone per 15-min window per vertical. Drives rider pre-positioning
  *and* dark-store restocking.
- **Dark-store siting**: where to open the next store. Uses population density, past demand
  heatmaps, real-estate cost.
- **Inventory planning**: which SKUs to stock at each store; restock frequency.
- **Dispatch ML**: rider ETA prediction, acceptance prediction, batching feasibility.
- **Search ranking**: per-vertical GBDT rankers + embeddings.
- **Fraud**: promo abuse, fake reviews, rider collusion.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐ has    ┌──────────────┐
   │  Restaurant  │1──────*│   Menu       │   (food vertical)
   │ - id         │        │ - version    │
   │ - name       │        └──────────────┘
   │ - location   │
   │ - rating     │
   └──────────────┘

   ┌──────────────┐ stocks  ┌──────────────────────────┐
   │  Dark Store  │1───────*│  Dark Store Inventory    │   (instamart vertical)
   │ - id         │         │ - store_id               │
   │ - location   │         │ - sku                    │
   │ - capacity   │         │ - on_hand                │
   │ - hours      │         │ - reserved               │
   └──────────────┘         └──────────────────────────┘

   ┌──────────────┐ places  ┌──────────────────────────────────────────┐
   │   Customer   │1───────*│   ORDER                                   │
   │ - id         │         │ - id, customer_id                         │
   │ - addresses  │         │ - vertical: FOOD / INSTAMART / GENIE      │
   │ - payment    │         │ - source_id (restaurant_id / store_id /   │
   │   methods    │         │   pickup-drop pair)                       │
   └──────────────┘         │ - items[]: {sku/item_id, qty, price}      │
                            │ - status: CREATED→...→DELIVERED           │
                            │ - drop_location (lat, lng)                │
                            │ - assigned_rider_id                       │
                            │ - total, fees, surge, promos              │
                            └──────────────┬───────────────────────────┘
                                           │ assigned
                                           ▼
                                  ┌────────────────┐
                                  │   Rider        │
                                  │ - id           │
                                  │ - location     │
                                  │ - status:      │
                                  │   FREE/ASSIGNED│
                                  │ - verticals:   │
                                  │   [food,insta] │
                                  │ - vehicle type │
                                  └────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                              | Why                                            |
| ------------------------------- | ---------------------------------- | ---------------------------------------------- |
| Food menus                      | MySQL (sharded) + Redis            | Read-heavy, versioned                          |
| Instamart catalog               | MySQL + Elasticsearch              | Per-SKU, per-store, search                     |
| **Dark-store inventory**        | **MySQL + strongly consistent reads** | Atomic conditional decrements (like e-commerce)|
| Cart                            | Redis                              | Short-lived, per-user                          |
| Orders                          | MySQL (sharded) + Kafka log        | ACID + audit                                   |
| Rider live locations            | In-memory geospatial index         | High write throughput, sub-ms queries          |
| Order state machine             | MySQL + Redis distributed lock     | Strong consistency on transitions              |
| Live tracking                   | WebSocket servers + Redis pub/sub  | Fan-out                                        |
| Promotions / campaigns          | MySQL + Redis (budget counters)    | Atomic budget decrement                        |
| Demand forecasts                | S3 (batch) + online KV              | Per-zone per-window predictions                |
| Event bus                       | Kafka                              | Decouple                                       |
| ML feature store                | Spark + online KV                  | Real-time features                             |

### 4.3 Why strong consistency for dark-store inventory?

Two customers order the last carton of milk from the same dark store at 9:00:01 PM. **Only one
should get it.** Soft reservation + reconcile would mean both are told "yes" and one is cancelled
later — terrible UX in a 10-min delivery. So inventory reservation at a dark store is strongly
consistent and atomic, just like Flipkart/Amazon. This is the key way quick-commerce is *more*
like e-commerce than food delivery.

### 4.4 Why a shared rider table?

Because the fleet is shared. A rider row carries their `verticals` eligibility (some riders only
do food; some do all three), current location, and current state. The assignment engine queries
this table (plus the geospatial index) to find the right rider for each order.

---

## 5. Request Flow — Ordering Groceries on Swiggy Instamart

Let's walk the 10-minute Instamart flow: customer in HSR Layout orders curd + milk + eggs at 9 PM.

```
CUSTOMER    EDGE      SEARCH     DARK STORE     CART     CHECKOUT   PAYMENT    ORDER      DISPATCH     RIDER     PICKER
   │          │         │         │              │          │          │         │           │            │         │
   │─search──▶│         │         │              │          │          │         │           │            │         │
   │ "curd"   │         │         │              │          │          │         │           │            │         │
   │          │─route──▶│         │              │          │          │         │           │            │         │
   │          │         │─resolve "curd" → SKU list + stock @ nearest dark store│          │           │            │         │
   │          │         │◀───────stock counts────│          │          │         │           │            │         │
   │◀─results─┤         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │─add curd + milk + eggs─────────────────────────────────▶│         │          │         │           │            │         │
   │          │         │         │              │  cart svc│          │         │           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │─checkout▶│         │         │              │          │          │         │           │            │         │
   │          │──────────────────────────────────────────────▶│        │         │           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │   [Checkout pipeline:]       │         │           │            │         │
   │          │         │         │              │   1. validate cart           │         │           │            │         │
   │          │         │         │              │   2. RESERVE inventory (atomic decr)─────│─────────▶│ (dark store DB)│         │
   │          │         │         │              │   3. apply promos + Swiggy One            │         │           │            │         │
   │          │         │         │              │   4. compute fees + surge + GST            │         │           │            │         │
   │          │         │         │              │   5. UPI collect ─────────────────────────│─────────│────────▶│ (PSP→NPCI) │         │
   │          │         │         │              │   6. create ORDER (CREATED) ─────────────│─────────│────────▶│            │         │
   │          │         │         │              │   7. fire DARK_STORE_PICK_EVENT ─────────│─────────│────────▶│──────────────────────▶│         │
   │◀──────"order placed, picking started"───────────────────────────────────│          │         │           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │   [PARALLEL: dispatch assigns rider]
   │          │         │         │              │          │          │         │   1. query pre-positioned riders @ store
   │          │         │         │              │          │          │         │   2. auto-assign rider_77          │            │         │
   │          │         │         │              │          │          │         │   3. emit RIDER_ASSIGNED           │            │         │
   │          │         │         │              │          │          │         │      ─────────────────────────────────────────▶│ (rider notified)
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │           │  [PICKER in dark store:]     │         │
   │          │         │         │              │          │          │         │           │   - receives pick list on handheld     │
   │          │         │         │              │          │          │         │           │   - walks shortest path (~2 min)       │
   │          │         │         │              │          │          │         │           │   - bags items, seals bag              │
   │          │         │         │              │          │          │         │           │   - hands to rider (or stages for rider)        │
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │           │   [Event: PAYMENT_SUCCESS ← PSP webhook]     │         │
   │          │         │         │              │          │          │◀────────│───────────│            │         │
   │          │         │         │              │          │          │ capture │           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │   [Event: PICKED_UP + rider rides]              │         │
   │◀──────live tracking: rider en route (4 min)────────────────────────────────────────│───────────│────────────│────────▶│
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │   [rider arrives, taps DELIVERED]              │         │
   │◀────"delivered in 9 min!" + receipt─────────────────────────────────────────────│           │            │         │
   │          │         │         │              │          │          │         │           │            │         │
   │          │         │         │              │          │          │         │   [Settlement: dark store finance + rider payout]
```

**Step-by-step:**

1. **Search.** Customer searches "curd." Instamart search resolves to SKUs, checks stock at the
   nearest dark store (chosen by customer location), ranks by popularity + promo + personalisation,
   suggests substitutes for any OOS.
2. **Add items.** Cart service writes to Redis.
3. **Checkout pipeline:**
   - **Validate** cart + prices.
   - **Reserve inventory** atomically at the chosen dark store — strong consistency, atomic
     decrement. If the last milk is gone, the customer sees "just sold out" immediately.
   - **Apply promotions** + Swiggy One benefits; decrement campaign budget atomically.
   - **Compute fees** (delivery + handling + surge + GST).
   - **Fire UPI collect**; order = `CREATED`, payment = `PENDING`.
   - **Emit `DARK_STORE_PICK_EVENT`** — this fires *in parallel* with payment, because the 10-min
     clock has already started.
4. **Dark store receives** the pick event. A picker's handheld shows the pick list, sequenced by
   store layout for shortest walking path. Picker walks, picks, bags — ~2 min.
5. **Dispatch assigns a rider** *in parallel* — ideally a rider is already parked at the store
   (pre-positioned). Auto-assign within ~30s.
6. **Payment callback** arrives from PSP. Verified, idempotency-checked, captured.
7. **Picker completes** → hands bag to rider → `PICKED_UP` event.
8. **Rider rides** ~4 min to the customer. Live tracking via WebSocket.
9. **Rider delivers**, taps `DELIVERED`. **Total time: ~9 min, under the 10-min budget.**

The parallelism between picking and rider assignment is the secret sauce. If dispatch waited for
the pick to finish (like food waits for cooking), you'd add 2 min and miss the 10-min promise.

### Food delivery flow (for comparison)

The food flow is the same as Zomato's (see `zomato.md` §5): order → restaurant accepts → rider
assigned → rider waits at restaurant → picks up → delivers in ~30 min. The difference is that
Swiggy's dispatch may **batch** the rider's trips across verticals (a food delivery followed by an
Instamart run) for fleet efficiency.

---

## 6. Scaling Strategy

### 6.1 Multi-vertical fleet pooling

The biggest scaling lever: **one rider fleet across three verticals**. This raises utilisation (a
rider is busy more of the time) and lets Swiggy serve more orders with fewer riders. The cost is
dispatch complexity — the assignment engine must reason about vertical eligibility, SLA
differences, and rider fatigue.

### 6.2 Dark-store network density

Quick-commerce scales by **adding dark stores**, not by adding central capacity. Each new store
within a dense cluster reduces the average ride time for that cluster. Swiggy (and competitors
Zepto, Blinkit) compete on store density. The siting decision is ML-driven.

```
   HSR Layout cluster (Bengaluru)
   ├── Dark store A (Sector 1) — 1.2 km radius
   ├── Dark store B (Sector 2) — 1.0 km radius
   └── Dark store C (Sector 6) — 1.5 km radius
```

### 6.3 Predictive rider pre-positioning

For Instamart, riders are parked at dark stores *before* orders arrive, based on demand forecasts.
Without this, the 10-min SLA is impossible. For food, riders are pre-positioned in zones.

### 6.4 Caching + read replicas

Same pattern as Zomato: CDN + Redis + MySQL read replicas for catalog and search. Live data
(rider location, order status) bypasses cache.

### 6.5 Zone sharding

Dispatch, geospatial index, and demand forecasting are sharded per zone — natural horizontal
scaling.

### 6.6 Event-driven everything

Every order transition, rider state change, inventory change, and payment event flows through
Kafka. Consumers react asynchronously.

### 6.7 IPL / weekend / rain peak capacity

- Pre-positioned riders per forecast.
- Dark stores pre-stocked for predicted demand (cold drinks for IPL, umbrellas for monsoon).
- Surge pricing to dampen excess demand.
- Feature freeze + war room during peak events.

### 6.8 Multi-region cloud

AWS + GCP across India regions; stateless services auto-scale.

---

## 7. Tech Stack

| Layer                       | Technology                                                    |
| --------------------------- | ------------------------------------------------------------- |
| Cloud                       | AWS + GCP (India regions)                                    |
| Edge                        | CDN, WAF, bot defence                                         |
| API gateway / BFF           | Custom JVM/Go gateway                                        |
| Languages                   | **Java**, **Go**, Python (ML), Kotlin, Swift                 |
| Frameworks                  | Spring Boot, gRPC, in-house Go services, Django/FastAPI (ML) |
| Databases                   | MySQL (sharded), Redis (Geo + inventory), Aerospike          |
| Search                      | Elasticsearch + custom ML ranker                              |
| Caching                     | Redis, Memcached                                             |
| Streaming                   | Apache Kafka                                                 |
| Geospatial                  | Redis GEO, S2 cells, custom indices                         |
| WebSocket                   | Custom WebSocket layer + Redis pub/sub                       |
| ML / data                   | Spark, TensorFlow / PyTorch, XGBoost, feature store          |
| Maps / routing              | Mapbox, OpenStreetMap, in-house routing                      |
| Container/runtime           | Kubernetes                                                    |
| Observability               | Prometheus, Grafana, ELK, OpenTelemetry                      |
| Payments                    | Razorpay, Juspay, PhonePe PG, in-house; UPI via NPCI         |
| Mobile                      | Native Android (Kotlin/Java), iOS (Swift)                    |
| Dark store ops              | Custom WMS-lite (pick orchestration, handhelds)              |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture (Instamart-style quick-commerce)

```
   ┌────────────┐  /search, /items/:id         ┌──────────────┐     ┌──────────────┐
   │  Customer  │◀────────────────────────────▶│  Flask/Node  │◀───▶│  Postgres    │
   │  app       │                              │   backend    │     │ (items,      │
   │            │                              └──────┬───────┘     │  inventory)  │
   │            │                                     │             └──────────────┘
   │            │                              ┌──────▼───────┐
   │            │                              │ Redis GEO    │
   │            │                              │ (rider locs) │
   │            │                              └──────────────┘
   │            │
   │            │  /order, /track (WebSocket)  ┌──────────────┐
   │            │◀────────────────────────────▶│  Order +     │
   │            │                              │  Dispatch    │◀───┐
   │            │                              │  service     │    │ ┌────────────┐
   │            │                              └──────────────┘    │ │ Rider sim  │
   │            │                                                  │ │ (Python)   │
   │            │                                                  │ └────────────┘
   │            │                                                  │
   │            │                                                  │ ┌────────────┐
   │            │                                                  └─│ Picker sim │
   │            │                                                    │ (Python)   │
   │            │                                                    └────────────┘
```

### 8.2 Step-by-step build

1. **Items + inventory.** Postgres: `items(id, name, price)`,
   `dark_store_inventory(store_id, item_id, on_hand, reserved)`. Seed a fake "dark store" with
   20 items.
2. **Search.** Postgres full-text or a small ES index. Filter by `on_hand - reserved > 0`.
3. **Cart.** Redis hash.
4. **Checkout pipeline.**
   - Reserve inventory atomically:
     ```sql
     UPDATE dark_store_inventory
     SET reserved = reserved + 1
     WHERE store_id=1 AND item_id=? AND (on_hand - reserved) >= 1
     RETURNING reserved;
     ```
   - On success: create order.
   - On failure: tell customer "just sold out."
   - Mock payment (no real UPI needed).
5. **Picker simulator.** A Python script that:
   - Polls for new orders.
   - Sleeps 2 min (simulating pick).
   - Emits `PICKED_UP`.
6. **Rider simulator.** A Python script that:
   - Registers its location with Redis GEO.
   - Polls for assigned orders.
   - When assigned, "rides" (updates lat/lng every 2s toward customer).
   - Emits `DELIVERED` on arrival.
7. **Dispatch.** On order create, query Redis GEO for nearest rider. Auto-assign. **Run rider
   assignment in parallel with picking** — that's the Instamart trick.
8. **Live tracking.** WebSocket server pushes rider location to customer every 2s.
9. **Frontend.** React + Leaflet map. Show items, cart, "place order," live rider dot, "delivered
   in 9 min."
10. **Measure.** Time from order to delivered. Try to hit <10 min (in simulation, accelerate the
    timers).

### 8.3 What you'll learn

- Why **quick-commerce needs dark stores** (central warehouses can't hit 10 min).
- Why **inventory reservation is atomic** (oversell = bad UX).
- Why **picking and dispatch run in parallel** (sequential = misses SLA).
- How a **shared rider fleet** across verticals changes dispatch.
- How **demand forecasting** drives pre-positioning.

### 8.4 Cost for a weekend build

- A laptop running Postgres + Redis + Flask + Leaflet = free.
- Real Swiggy's costs are dominated by **dark-store rent + staff** and **rider payouts**.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered          | Why Swiggy chose it                                  |
| ----------------------------------------------- | ------------------------------- | ---------------------------------------------------- |
| **Multi-vertical shared rider fleet**           | One fleet per vertical          | Higher utilisation, fewer riders needed              |
| **Dark stores within 1.5 km of demand**         | Central warehouses + long rides | Only way to hit 10-min delivery                      |
| **Pre-positioned riders at dark stores**        | Pure reactive dispatch          | Without it, 10-min SLA impossible                    |
| **Parallel picking + dispatch**                 | Sequential                      | Saves ~2 min — the difference between 10 and 12 min  |
| **Strongly consistent dark-store inventory**    | Eventual consistency            | Two customers ordering the last milk → only one wins |
| **Auto-assign dispatch**                        | Broadcast                       | Speed at scale                                       |
| **MySQL for inventory + orders**                | NoSQL                           | ACID + atomic decrements + relational joins          |
| **In-memory geospatial index**                  | DB-backed                       | High write throughput, sub-ms queries                |
| **Surge pricing per zone + category**           | Flat pricing                    | Balances supply/demand; rain handling                |
| **Swiggy One subscription**                     | Per-order only                  | Recurring revenue + retention                        |

### The deepest trade-off

**10-min delivery vs. unit economics.** Quick-commerce's 10-min promise is a customer delight
but a margin disaster. The dark-store rent + picker staff + rider payout per order often exceeds
the margin on a ₹500 grocery basket. Swiggy (and rivals Zepto, Blinkit) are betting that (a)
scale brings down per-order cost, (b) customers will pay a membership (Swiggy One) for the
convenience, and (c) basket sizes will grow over time. The architectural choices (dark stores,
pre-positioned riders, parallel pipeline) are all *in service of the 10-min promise* — even at
the cost of unit economics. Whether this bet pays off is the multi-billion-dollar question.

---

## 10. Common Interview Questions

**Q1: Design Swiggy / a food delivery + quick-commerce platform.**
Walk the three-actor flow for food (like Zomato), then introduce Instamart as the twist: dark
stores, 10-min SLA, parallel picking + dispatch, strongly consistent inventory. Decompose:
catalog (per vertical), search (per vertical), order state machine, multi-vertical dispatch,
live tracking, settlement.

**Q2: How do you deliver groceries in 10 minutes?**
You can't from a central warehouse. Put **dark stores** within 1.5 km of dense demand. Pre-position
riders at the store. Run **picking and rider assignment in parallel**. Pick in 2 min, ride in 4
min, handover in 1 min = ~7–9 min total.

**Q3: How do you prevent overselling the last carton of milk?**
Strongly consistent, atomic conditional decrement in the dark-store inventory DB. SQL
`UPDATE ... WHERE on_hand - reserved >= 1 RETURNING`. Only one customer wins; others see "just
sold out."

**Q4: How does the multi-vertical dispatch work?**
A shared rider pool with vertical eligibility flags. The assignment engine treats it as bipartite
matching (orders ↔ riders) per zone, weighted by SLA tightness (Instamart weighted higher than
food). Pre-positioning for Instamart is separate from food.

**Q5: Why pre-position riders at dark stores?**
If dispatch waits for an order to find a rider, you lose 1–2 min — the difference between 10 and
12 min. Pre-positioned riders (based on demand forecast) start the ride immediately on order.

**Q6: How do you handle IPL / weekend / rain peak?**
Demand forecasts drive rider pre-positioning and dark-store restocking. Surge pricing dampens
excess demand. Feature freeze + war room. Pre-warmed capacity.

**Q7: How is Instamart inventory different from food menus?**
Food menus don't track per-item counts (kitchen capacity is fluid). Instamart tracks exact
per-SKU per-store counts and reserves atomically — it's the e-commerce inventory problem at
hyperlocal scale.

**Q8: How do you show the rider live on the customer's map?**
Rider GPS every 3–5s → Kafka → live-tracking service → WebSocket push to customer. Sticky
sessions on the WS server.

**Q9: How do you decide where to open the next dark store?**
ML model on population density, historical demand heatmaps, real-estate cost, competitor
presence, delivery-time targets. Optimise for "max demand within 1.5 km radius per ₹ of rent."

**Q10: How does Swiggy One (subscription) affect the architecture?**
Membership state is read at checkout (free delivery, no surge, selected benefits). It's a
per-customer flag with TTL; cached in Redis. The architecture impact is minimal — it's a
pricing/business-rule layer.

**Q11: What happens if a dark store reaches capacity?**
The store's capacity model detects overload; ETAs slip ("delivering in 20 min") or the store
stops accepting orders ("high demand — try later"). Demand is rerouted to nearby stores if
possible.

**Q12: How do you batch a rider's trips across verticals?**
The assignment engine considers a rider's current trip end location + ETA; if a new order's
pickup is near the rider's next-free location and time, it can be batched. Batching is more
common for food (longer SLA) than Instamart (tight SLA).

---

## Further reading

- Swiggy engineering blog (engineering.swiggy.com) — talks on dispatch, Instamart, ML.
- Zepto and Blinkit engineering content — competitor perspectives on quick-commerce.
- Uber engineering blog — the analogous dispatch problem.
- Redis GEO / S2 geometry docs — geospatial indexing.
- "Dark store" economics analyses (CB Insights, Jefferies) — the unit-economics context.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures, quarterly reports, and
engineering talks.*

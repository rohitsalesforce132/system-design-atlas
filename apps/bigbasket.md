# BigBasket — System Design Atlas

> **One-line summary:** BigBasket is India's largest full-stack online grocery platform — it owns
> the customer experience, the warehouse (DC), the inventory, and the last-mile delivery — operating
> a **scheduled-slot** model (not instant) with next-day and same-day delivery from city-level
> distribution centres, a hyperlocal **bb Express** (60–90 min) layer on top, and a milk-subscription
> business (bb Daily) — making it more like an e-commerce + logistics company than a food-tech
> marketplace.

---

## 1. Overview & Scale Numbers

BigBasket is the **quietly different** one in this set. Where Zomato/Swiggy are "instant
marketplaces" (match customer ↔ restaurant ↔ rider in 30 min), BigBasket is **a vertically
integrated grocery retailer with scheduled delivery** — closer in spirit to Flipkart/Amazon than
to Swiggy Instamart, but with grocery-specific twists (perishables, substitutions, milk
subscriptions).

Three things make BigBasket architecturally distinct:

1. **Inventory ownership.** BigBasket buys and holds grocery inventory in its own DCs (distribution
   centres). It's not a pure marketplace (though it has a marketplace tier). This means **warehouse
   + inventory management** is core, like Flipkart's eKart — not delegated to restaurants.
2. **Scheduled-slot delivery.** The default product is **next-day delivery in a chosen 2-hour
   slot** ("tomorrow 7–9 AM"). This decouples *ordering* from *fulfillment* — orders accumulate
   overnight, get picked at dawn, routed to routes, delivered in waves. This is fundamentally
   different from "instant" delivery and lets BigBasket be far more capital-efficient per order.
3. **bb Daily (milk subscription).** A separate business: customers subscribe to daily milk + bread
   + eggs, delivered every morning before 7 AM by a dedicated micro-route fleet. This is a
   **high-frequency, recurring, route-optimised** logistics problem — a different beast from
   on-demand.

### The numbers

| Metric                                        | Approximate value              | Why it matters                                            |
| --------------------------------------------- | ------------------------------ | -------------------------------------------------------- |
| Registered customers                          | ~30M+                          | Largely metro + Tier-1; expanding to Tier-2/3             |
| Cities active                                 | ~25+ metro + Tier-1, growing   | Each city has its own DC + fleet                          |
| SKUs in catalog                               | ~20,000–30,000 per DC          | Grocery is broad: fresh, staples, FMCG, household         |
| Distribution centres (DCs)                    | dozens, one+ per metro         | Each is a 50,000–100,000 sq ft warehouse                  |
| Orders per day                                | ~300,000+                      | Concentrated in morning + evening slots                   |
| Average order value (basket size)             | ~₹1,500–2,000                  | Much higher than quick-commerce (₹400–600) — weekly stockup |
| Delivery slots per day                        | ~6–8 (2-hour windows)          | "Tomorrow 7–9 AM, 9–11 AM, ..."                          |
| bb Daily subscriptions                         | millions of daily deliveries   | Milk + bread + eggs, every morning, before 7 AM          |
| bb Express (instant) share                    | growing in metros              | 60–90 min from micro-DCs / dark stores                   |
| Delivery executives                           | ~50,000+                       | Mixed: scheduled-slot route drivers + bb Daily + express |
| DCs / micro-DCs (for bb Express)              | hundreds in metros             | Smaller format, closer to demand                         |
| Fill rate (substitution rate)                 | ~90%+ target                   | Grocery substitutions are common (brand swap)             |
| Perishables shrinkage target                  | <2–3%                          | Fresh produce spoilage is a margin killer                 |

### The product goal

A customer in **Bengaluru** opens BigBasket on Sunday evening, browses "atta, rice, cooking oil,
tomatoes, onions, milk," sees ~20,000 SKUs with live prices, adds ~30 items to cart (~₹1,800),
picks "tomorrow 7–9 AM" delivery, pays via UPI, and at 7:15 AM Monday a delivery executive hands
them two bags of groceries at their door. Behind the scenes: order was batched overnight with
~50 other orders for the same route, picked at the DC at 4 AM, loaded onto a route truck at 5 AM,
delivered in a wave between 7–9 AM. Perishables picked fresh that morning. Payment reconciled;
inventory decremented; next week's demand forecast updated.

### The analogy: it's Flipkart for groceries, with a milkman bolted on

BigBasket's core (browse → cart → scheduled checkout → DC pick → route delivery) is **e-commerce
logistics applied to groceries** — same as Flipkart but with smaller baskets, perishables, and
2-hour delivery slots instead of 2-day. The **bb Daily** subscription layer is a separate,
route-optimised dairy logistics business layered on top.

---

## 2. High-Level Architecture

```
   ┌───────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │  CUSTOMER APP     │  │  DC STAFF APP      │  │  DELIVERY EXEC APP │
   │  (bb / bb Daily / │  │  (pick / pack /    │  │  (route manifest,  │
   │   bb Express)     │  │   load)            │  │   navigate, deliver)│
   └────────┬──────────┘  └─────────┬──────────┘  └──────────┬─────────┘
            │  HTTPS                                    │                     │
            ▼                                           │                     │
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       API GATEWAY / BFF                             │
   │        (TLS, auth, rate limit, channel-aware routing)              │
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
   ┌──────────────┐       ┌──────────────┐         ┌──────────────────┐
   │ Catalog      │       │ Search +     │         │ Slot / Capacity  │
   │ (SKUs,       │       │ Ranking      │         │ Engine           │
   │  categories) │       │              │         │ (per DC, per slot)│
   └──────────────┘       └──────────────┘         └──────────────────┘
                                 │
   ┌─────────────────────────────▼──────────────────────────────────────┐
   │                       ORDER PLANE                                  │
   │   Cart  •  Checkout  •  Payment (UPI/Card/COD/Wallet)              │
   │   Pricing + Promos + GST    •   Substitution Engine               │
   │   Three pipelines: SCHEDULED (bb) / INSTANT (bb Express) /         │
   │                     SUBSCRIPTION (bb Daily)                        │
   └─────────────────────────────┬──────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       WAREHOUSE (DC) MANAGEMENT                     │
   │   Inventory  •  Pick Path Optimisation  •  Perishable handling     │
   │   Substitution picker  •  Pack / manifest  •  Cold chain           │
   │   DC capacity + slot planning                                       │
   └─────────────────────────────┬───────────────────────────────────────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       ROUTING & LAST-MILE                          │
   │   Order batching (by slot + zone)  •  Vehicle routing (VRP)        │
   │   Route manifests  •  Delivery wave scheduling                     │
   │   bb Daily micro-routes  •  bb Express rider dispatch              │
   │   Returns / re-delivery                                             │
   └─────────────────────────────────────────────────────────────────────┘
```

### The key abstraction: the slot + batch

Where Zomato/Swiggy think in *orders* (one order → one rider → one delivery), BigBasket thinks
in **slots and batches**:

```
   Slot: Tomorrow 7–9 AM, Bengaluru South zone
   ├── Order A (customer_1): 30 items, ₹1,800
   ├── Order B (customer_2): 22 items, ₹1,400
   ├── Order C (customer_3): 45 items, ₹2,600
   ├── ... (~50 orders in this slot+zone)
   │
   └── BATCH: all picked at DC at 4 AM, loaded onto 3 route trucks at 5 AM,
       delivered in a wave 7–9 AM. One truck serves ~15 stops.
```

This batching is the **economic engine** of scheduled delivery. A rider delivering 15 orders in
one 2-hour route is far more efficient than 15 separate Swiggy-style trips.

### Three order state machines

BigBasket runs **three different** order lifecycles:

```
   SCHEDULED (bb core):
   [CREATED] → [SLOT_RESERVED] → [PAYMENT_OK] → [BATCHED]
      → [PICKING at DC] → [PACKED] → [LOADED] → [OUT_FOR_DELIVERY]
      → [DELIVERED]

   INSTANT (bb Express, 60–90 min):
   [CREATED] → [PAYMENT_OK] → [PICKING at micro-DC] → [RIDER_ASSIGNED]
      → [PICKED_UP] → [OFD] → [DELIVERED]    (similar to Swiggy Instamart)

   SUBSCRIPTION (bb Daily):
   [SUBSCRIPTION_CREATED] → (every night: auto-generate order) →
      → [PICKED] → [LOADED to micro-route] → [DELIVERED before 7 AM]
      → (customer can modify/pause by 10 PM previous night)
```

---

## 3. Detailed Component Breakdown

### 3.1 Catalog service

Owns the SKU master: name, brand, category (staples, fresh produce, FMCG, dairy, household,
personal care), attributes (veg/non-veg, organic, pack size), images, **barcode**, **MRP**,
**HSN code** for GST, and shelf-life (for perishables). Catalog is sharded by `sku_id`; hot SKUs
(rice, oil, milk) cached aggressively.

A grocery-specific concern: **pack sizes and variants**. "Aashirvaad Atta" comes in 1kg, 2kg, 5kg,
10kg packs — each is a distinct SKU with its own price and inventory.

### 3.2 Search & Ranking

Ranking signals:
- Relevance (text + category embeddings — "atta" should match "whole wheat flour")
- In-stock at the customer's DC
- Popularity (top-sellers in the customer's zone)
- Promotions + bundle deals ("buy 2 get 1 free")
- Personalisation (the customer's recurring basket)
- Fresh/seasonal surfacing (mangoes in summer)

```
   query: "cooking oil"
        │
        ▼
   ┌──────────────────────────────────────────────┐
   │   SEARCH SERVICE                             │
   │  1. Resolve query → SKUs (synonyms, Hindi)   │
   │  2. Filter: in-stock at Bengaluru DC         │
   │  3. Rank: popularity, promo, personalisation │
   │  4. Group by brand/variant                   │
   │  5. Return: SKU cards with pack sizes        │
   └──────────────────────────────────────────────┘
```

### 3.3 Slot & capacity engine

A genuinely grocery-specific component. Each DC has a finite **pick capacity** per slot (how many
orders it can pick in the pre-dawn window) and a finite **delivery capacity** per slot (how many
routes it can run). The slot engine:

- Tracks capacity per `(DC, slot)` pair.
- Reserves capacity at checkout (atomic, like inventory).
- Closes a slot when capacity is full ("7–9 AM full — try 9–11 AM").
- Re-opens capacity if an order is cancelled.

```
   ┌──────────────────────────────────────────────┐
   │   SLOT ENGINE                                │
   │   DC: Bengaluru South                        │
   │   ┌─────────────────┬───────────┬───────────┐│
   │   │ Slot            │ Pick cap  │ Deliv cap ││
   │   ├─────────────────┼───────────┼───────────┤│
   │   │ Mon 7–9 AM      │ 220 / 250 │ 220 / 250 ││  ← almost full
   │   │ Mon 9–11 AM     │ 80 / 200  │ 80 / 200  ││  ← available
   │   │ Mon 5–7 PM      │ 0 / 200   │ 0 / 200   ││  ← empty
   │   └─────────────────┴───────────┴───────────┘│
   └──────────────────────────────────────────────┘
```

### 3.4 Substitution engine

Grocery reality: ~5–10% of items are out of stock at pick time (perishables vary, supply is
lumpy). BigBasket's substitution engine:

- At order time, the customer can set **substitution preferences** per item: "allow substitutes"
  or "don't substitute" or "refund if OOS."
- At pick time, if an item is OOS, the picker sees a suggested substitute (same category, similar
  pack size, equal-or-better brand) and accepts/rejects.
- The substitute is charged at its own price; the order total is adjusted.

This is a critical UX+ops component — a high substitution rate (>10%) erodes customer trust.

### 3.5 Order service + state machine

Owns the three state machines (scheduled, instant, subscription). Key properties:
- **Idempotency** (duplicate checkout → one order).
- **Slot reservation** (atomic, for scheduled).
- **Subscription cron** (for bb Daily: nightly job generates the next morning's orders from active
  subscriptions, with customer edits applied until 10 PM cutoff).
- **Modification window** (customers can edit/cancel a scheduled order until ~midnight before the
  slot).

### 3.6 Cart & Checkout

Cart is per-user (Redis). Checkout:
1. Validate cart + prices + pack sizes.
2. **Reserve slot capacity** (atomic).
3. Apply promotions + BB Wallet + loyalty (bb Star).
4. Compute fees (delivery fee — often free above ₹1,200 — + handling + surge if any + GST).
5. Payment: UPI (async) / Card / Wallet / COD (common in grocery).
6. Create order; emit events.
7. For subscriptions: customer sets a recurring schedule; the order is auto-generated nightly.

### 3.7 Warehouse (DC) management

This is where BigBasket is *most like Flipkart/Amazon* and *least like Zomato/Swiggy*. The DC is a
full warehouse:

- **Inventory**: per-SKU per-DC counts, with **batch tracking** for perishables (each batch has a
  manufacture/expiry date). FEFO (First-Expired-First-Out) picking for fresh items.
- **Pick path optimisation**: SKUs are arranged by category aisle; pick lists are sequenced for
  shortest walking path. A single pick run can serve multiple orders (batch picking).
- **Perishable handling**: cold storage for dairy/meat; temperature monitoring; spoilage alerts.
- **Receiving + putaway**: daily inbound from suppliers; quality check; putaway to bin locations.
- **Pack / manifest**: items packed by fragility/perishability; route manifest printed.

### 3.8 Routing & last-mile

- **Order batching**: orders for the same `(slot, zone)` are batched into routes.
- **Vehicle Routing Problem (VRP)**: solve for the optimal set of routes that delivers all orders
  in the slot within the time window, minimising distance + vehicles.
- **Route manifests**: each driver gets a manifest (sequence of stops, items per stop).
- **bb Daily micro-routes**: separate, high-frequency, fixed routes — a milkman-style loop.
- **bb Express dispatch**: instant rider dispatch, like Swiggy Instamart (geospatial index,
  auto-assign).

### 3.9 Pricing & Promotions

- **MRP compliance**: in India, MRP (Maximum Retail Price) is legally enforced; BigBasket cannot
  charge above MRP. Discounts are below MRP.
- **Bundle deals**: "buy 2 get 1," combo packs.
- **BB Wallet + bb Star loyalty**.
- **Vendor-funded promotions**: FMCG brands fund deals (like Flipkart's bank offers).

### 3.10 ML / data platform

- **Demand forecasting**: per DC per SKU per day. Drives procurement + DC staffing + slot capacity.
  Grocery demand is regular (weekly cycles) but spiky (festivals, lockdowns).
- **Substitution prediction**: which SKUs will be OOS tomorrow morning → pre-suggest substitutes.
- **Route optimisation**: VRP solver + ML for travel-time prediction.
- **Inventory optimisation**: how much of each perishable to stock (minimise shrinkage vs.
  stockouts).
- **Churn prediction**: subscription customers at risk of pausing.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐ stocked  ┌──────────────────────────────┐
   │  DC          │1────────*│  DC Inventory                │
   │ - id         │          │ - dc_id                      │
   │ - city       │          │ - sku_id                     │
   │ - location   │          │ - batch_id (perishables)     │
   │ - capacity   │          │ - on_hand                    │
   └──────────────┘          │ - reserved                   │
                             │ - expiry_date                │
                             └──────────────────────────────┘

   ┌──────────────┐ lists    ┌──────────────┐
   │  Supplier    │1────────*│   SKU        │
   │ - id         │          │ - id         │
   │ - name       │          │ - name       │
   │ - GSTIN      │          │ - brand      │
   └──────────────┘          │ - category   │
                             │ - pack_size  │
                             │ - MRP        │
                             │ - HSN code   │
                             │ - shelf_life │
                             └──────────────┘

   ┌──────────────┐ places  ┌──────────────────────────────────────────┐
   │   Customer   │1───────*│   ORDER                                   │
   │ - id         │         │ - id, customer_id                         │
   │ - addresses  │         │ - channel: SCHEDULED / EXPRESS / DAILY    │
   │ - payment    │         │ - items[]: {sku, qty, price, substitute?} │
   │   methods    │         │ - status: CREATED→...→DELIVERED           │
   │ - subscriptions│       │ - dc_id (fulfilling DC)                  │
   └──────┬───────┘         │ - slot (for scheduled)                    │
          │                 │ - route_id (for delivery)                 │
          │ has             │ - drop_address                            │
          ▼                 │ - payment_mode                            │
   ┌──────────────┐         └──────────────────────────────────────────┘
   │ Subscription │ (bb Daily)
   │ - id         │
   │ - customer_id│
   │ - sku        │
   │ - frequency  │
   │ - status     │
   └──────────────┘
```

### 4.2 Storage choices

| Data                            | Store                              | Why                                            |
| ------------------------------- | ---------------------------------- | ---------------------------------------------- |
| SKU catalog                     | MySQL (sharded) + Redis cache      | Relational, read-heavy                         |
| Search index                    | Elasticsearch + ranker             | Full-text + ranking                            |
| **DC inventory**                | **MySQL + strongly consistent reads** | Atomic decrements, batch tracking (perishables) |
| Cart                            | Redis                              | Short-lived, per-user                          |
| Slot capacity                   | MySQL + Redis (atomic counters)    | Atomic slot reservation                        |
| Orders                          | MySQL (sharded) + Kafka log        | ACID + audit                                   |
| Subscriptions (bb Daily)         | MySQL + cron scheduler             | Recurring state, nightly batch generation      |
| Route manifests                 | MySQL + Kafka                      | Per-route, per-slot                            |
| Rider live locations (Express)  | In-memory geospatial index         | High write throughput (for bb Express)         |
| Payments                        | MySQL + PSP settlement files       | ACID, reconciliation                           |
| Demand forecasts                | S3 (batch) + online KV              | Per-DC per-SKU per-day                         |
| Event bus                       | Kafka                              | Decouple                                       |
| ML feature store                | Spark + online KV                  | Forecasting features                           |

### 4.3 Why strongly consistent DC inventory?

Same reason as Flipkart/e-commerce: two customers order the last 5kg bag of Aashirvaad Atta → only
one should get it; the other gets a substitution suggestion. Atomic conditional decrement.

### 4.4 Why MySQL + a cron scheduler for subscriptions?

bb Daily is **recurring state**. Each subscription is a row with frequency, next_delivery_date,
status. A nightly cron job (or scheduler like Airflow) scans active subscriptions and generates
tomorrow's orders in a batch. This is simpler and more auditable than a real-time event-driven
approach for a nightly batch process.

### 4.5 Why a separate route manifest table?

Routes are *derived* from orders (batched by slot + zone) but have their own lifecycle (assigned
to a driver, loaded, out-for-delivery, completed). Treating them as first-class entities lets the
routing service optimise and re-optimise independently of orders.

---

## 5. Request Flow — Ordering Groceries on BigBasket (Scheduled Slot)

Let's walk the canonical flow: customer in Bengaluru orders a weekly stockup on Sunday evening for
Monday 7–9 AM delivery.

```
CUSTOMER    EDGE      SEARCH    CATALOG    SLOT ENG    CART     CHECKOUT   PAYMENT    ORDER       DC WMS      ROUTING     DELIVERY
   │          │         │         │          │          │          │          │         │           │            │           │
   │─search──▶│         │         │          │          │          │          │         │           │            │           │
   │ "atta"   │         │         │          │          │          │          │         │           │            │           │
   │          │─route──▶│         │          │          │          │          │         │           │            │           │
   │          │         │─resolve → SKUs────▶│          │          │          │         │           │            │           │
   │          │         │◀─metadata──────────┤          │          │          │         │           │            │           │
   │          │         │─in-stock @ BLR DC?───────────────────────────────────────────▶│ (inv svc) │            │           │
   │          │         │◀─counts────────────────────────────────────────────────────────│            │            │           │
   │◀─results─┤         │         │          │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │          │         │           │            │           │
   │─add ~30 items────────────────────────────────────────────────▶│          │         │           │            │           │
   │          │         │         │          │          │ cart svc │          │         │           │            │           │
   │          │         │         │          │          │ (Redis)  │          │         │           │            │           │
   │          │         │         │          │          │          │          │         │           │            │           │
   │─choose slot "Mon 7–9 AM"──────────────────────────────────────▶│         │         │           │            │           │
   │          │         │         │          │─reserve slot capacity (atomic)──────────────────────────────────▶│            │           │
   │          │         │         │          │◀──reserved ok───────────────────────────────────────────────────│            │           │
   │          │         │         │          │          │          │         │           │            │           │
   │─checkout▶│         │         │          │          │          │          │         │           │            │           │
   │          │──────────────────────────────────────────────────────▶│        │         │           │            │           │
   │          │         │         │          │          │          │          │         │           │            │           │
   │          │         │         │          │   [Checkout pipeline:]       │         │           │            │           │
   │          │         │         │          │   1. validate cart + prices  │         │           │            │           │
   │          │         │         │          │   2. apply promos + BB Wallet│         │           │            │           │
   │          │         │         │          │   3. compute fees + GST      │         │           │            │           │
   │          │         │         │          │   4. UPI collect ────────────│─────────│────────▶│ (PSP→NPCI) │            │           │
   │          │         │         │          │   5. create ORDER (CREATED)──│─────────│────────▶│            │            │           │
   │◀──────"order placed, delivering Mon 7–9 AM"───────────────────────────│          │         │           │            │           │
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │   [Event: ORDER_CREATED → Kafka (batched overnight)]
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │   ... hours pass ...               │           │
   │          │         │         │          │          │          │         │   [3 AM Mon: batch job picks all Mon 7–9 AM orders]
   │          │         │         │          │          │          │         │      ▼     │            │           │
   │          │         │         │          │          │          │         │   ROUTING: batch orders by zone, solve VRP
   │          │         │         │          │          │          │         │   → assign route_ids, generate manifests──────────▶│
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │   [4 AM Mon: DC WMS starts picking]
   │          │         │         │          │          │          │         │   - batch pick across orders in the route         │
   │          │         │         │          │          │          │         │   - FEFO for perishables (use earliest-expiry batch)│
   │          │         │         │          │          │          │         │   - substitutions where OOS (picker decides)       │
   │          │         │         │          │          │          │         │   - pack by route, print manifest                 │
   │          │         │         │          │          │          │         │   [5 AM: LOADED event]                            │
   │          │         │         │          │          │          │         │      ▼     │            │           │
   │          │         │         │          │          │          │         │      └────│────────────│──────────▶│ (driver gets manifest, loads truck)
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │   [7 AM: OUT_FOR_DELIVERY]                       │
   │◀──────"out for delivery" + ETA───────────────────────────────────────────│           │            │           │
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │           │   [driver follows route: stop 1, stop 2, ...]
   │          │         │         │          │          │          │         │           │            │   ─arrives at customer─▶│
   │          │         │         │          │          │          │         │           │            │           │  ─hands over bags, taps DELIVERED─▶│
   │          │         │         │          │          │          │         │◀──DELIVERED event───────────────────────────────────│
   │◀────"delivered!" + invoice (GST)─────────────────────────────────────────│           │            │           │
   │          │         │         │          │          │          │         │           │            │           │
   │          │         │         │          │          │          │         │   [For COD: cash collected; reconciliation later]
   │          │         │         │          │          │          │         │   [Inventory decremented; demand forecast updated]
```

**Step-by-step:**

1. **Search.** Customer searches "atta." Resolved to SKUs (synonyms, Hindi), filtered to in-stock
   at Bengaluru DC, ranked by popularity + promo + personalisation.
2. **Add items.** ~30 items added to cart (Redis).
3. **Choose slot.** Customer picks "Mon 7–9 AM." Slot engine **reserves capacity atomically** — if
   the slot is nearly full, the customer sees "almost full" or "try 9–11 AM."
4. **Checkout pipeline:**
   - Validate cart + prices + pack sizes.
   - Apply promotions + BB Wallet.
   - Compute fees + GST (GST varies by category — 5% on staples, 18% on household).
   - Fire UPI collect; order = `CREATED`, payment = `PENDING`.
5. **Payment success** → order `CONFIRMED`. Slot capacity committed.
6. **`ORDER_CREATED` event** → Kafka. The order joins the batch for "Mon 7–9 AM, Bengaluru South."
7. **Hours pass.** (The defining feature of scheduled delivery — no rush.)
8. **3 AM Monday: batch job runs.** All confirmed orders for the 7–9 AM slot are batched by zone.
   The **routing service** solves VRP: group ~50 orders into ~3 routes of ~15 stops each, optimise
   stop sequence, generate route manifests.
9. **4 AM Monday: DC picking starts.**
   - Pickers receive **batch pick lists** (one pick run collects items across multiple orders in a
     route, sequenced by aisle for shortest walking path).
   - **FEFO** for perishables: pick the earliest-expiry batch first (reduce spoilage).
   - If an item is OOS, the picker sees a **suggested substitute** (from the substitution engine)
     and accepts or rejects; the order total is adjusted.
   - Pack by route; print manifest + GST invoice.
10. **5 AM: LOADED.** Driver receives manifest on app; loads the truck.
11. **7 AM: OUT_FOR_DELIVERY.** Customer gets "out for delivery" + ETA.
12. **Driver follows the route:** stop 1 → stop 2 → ... → customer. At each stop, hands over
    bags, customer confirms, driver taps DELIVERED.
13. **DELIVERED event** → `DELIVERED` state. For COD, cash collected and reconciled later.
    Inventory decremented (true on-hand). Demand forecast updated with this order's data.

### bb Daily (subscription) variant

- Customer subscribes to "1L milk, daily, before 7 AM" + "bread, Mon/Wed/Fri."
- A **nightly cron** (10 PM cutoff) generates tomorrow's orders from active subscriptions,
  applying any customer pauses/modifications made before 10 PM.
- Orders are routed to **micro-routes** (fixed loops, like a milkman's route) — highly optimised
  because the route is the same every day.
- Delivered before 7 AM by a dedicated fleet.
- Payment: prepaid wallet auto-debit, or weekly COD settlement.

### bb Express (instant) variant

- Customer orders from a smaller catalog, served from a **micro-DC / dark store**.
- Flow mirrors Swiggy Instamart: order → pick → rider assigned (parallel) → 60–90 min delivery.
- See `swiggy.md` §5 for the analogous flow.

---

## 6. Scaling Strategy

### 6.1 City-level DC replication

Each city is a mostly-independent operational unit with its own DC(s), fleet, and slot engine.
Scaling = "open a DC in a new city." This is operationally heavy (each DC needs real estate,
staff, supplier relationships) but architecturally clean.

### 6.2 Batch efficiency (scheduled delivery's superpower)

The scheduled-slot model lets BigBasket **batch** ~50 orders into one route, making per-order
delivery cost a fraction of Swiggy Instamart's. This is why BigBasket can be profitable on a
₹1,800 basket where quick-commerce struggles on a ₹500 basket.

### 6.3 Caching + read replicas

Catalog and search are heavily cached (CDN + Redis + MySQL read replicas). Inventory counts are
less cacheable (they change), but hot SKUs get short-TTL cache entries.

### 6.4 Event-driven batch processing

Orders flow into Kafka overnight; batch consumers (routing, picking, manifest generation) process
them in waves. This decouples order placement (anytime) from fulfillment (3–7 AM window).

### 6.5 VRP at scale

Routing ~50 orders per slot per zone into optimal routes is a well-studied problem (Vehicle
Routing Problem with Time Windows). Solvers range from greedy heuristics (fast, good-enough) to
metaheuristics (simulated annealing, genetic algorithms) for tighter optimisation. BigBasket runs
these nightly, so solver runtime isn't on the critical path.

### 6.6 Festival / lockdown peak

- **Diwali / festive season**: demand spikes for sweets, dry fruits, gifts. DCs pre-stock;
  capacity expanded.
- **Lockdowns** (COVID era): 5–10× demand surge. BigBasket throttled via slot availability
  ("no slots today"), queueing, and prioritisation. The slot model is a natural throttle — you
  can't oversell capacity you don't have.

### 6.7 Perishable cold chain

Fresh produce + dairy + meat require cold storage at the DC and cold-chain transport. This is a
capital expense but enables the fresh category. Spoilage is monitored closely (shrinkage <2–3%
target).

### 6.8 Multi-cloud + DR

BigBasket (post-Tata acquisition) runs on cloud infra across India regions, with DR for orders and
payments.

---

## 7. Tech Stack

| Layer                       | Technology                                                    |
| --------------------------- | ------------------------------------------------------------- |
| Cloud                       | AWS / GCP (India regions) + Tata Digital infra               |
| Edge                        | CDN, WAF                                                      |
| API gateway / BFF           | Custom JVM/Go gateway                                        |
| Languages                   | **Java**, Python, Go, Kotlin, Swift                          |
| Frameworks                  | Spring Boot, Django/FastAPI (ML), gRPC                       |
| Databases                   | MySQL (sharded), Redis, Aerospike                            |
| Search                      | Elasticsearch + custom ranker                                 |
| Caching                     | Redis, Memcached                                             |
| Streaming                   | Apache Kafka                                                 |
| Batch / scheduling          | Apache Airflow / in-house scheduler (for nightly jobs)       |
| Geospatial (Express)        | Redis GEO, S2 cells                                          |
| ML / data                   | Spark, TensorFlow / PyTorch, XGBoost, feature store          |
| Routing (VRP)               | Custom + OR-Tools / open-source VRP solvers                  |
| Container/runtime           | Kubernetes                                                    |
| Observability               | Prometheus, Grafana, ELK, OpenTelemetry                      |
| Payments                    | Razorpay, Juspay, PhonePe PG, in-house; UPI via NPCI         |
| Mobile                      | Native Android (Kotlin/Java), iOS (Swift)                    |
| DC ops                      | Custom WMS, handheld devices, cold-chain monitoring          |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture (scheduled-slot grocery)

```
   ┌────────────┐  /search, /items/:id         ┌──────────────┐     ┌──────────────┐
   │  Customer  │◀────────────────────────────▶│  Flask/Node  │◀───▶│  Postgres    │
   │  app       │                              │   backend    │     │ (items, DC   │
   │            │                              └──────┬───────┘     │  inventory,  │
   │            │                                     │             │  slots)      │
   │            │                              ┌──────▼───────┐     └──────────────┘
   │            │                              │ Redis        │
   │            │                              │ (slot caps,  │
   │            │                              │  cart)       │
   │            │                              └──────────────┘
   │            │
   │            │  /order                      ┌──────────────┐     ┌──────────────┐
   │            │◀────────────────────────────▶│  Order +     │◀───▶│  Postgres    │
   │            │                              │  Slot service│     │  (orders)    │
   │            │                              └──────┬───────┘     └──────────────┘
   │            │                                     │
   │            │                              ┌──────▼───────┐
   │            │                              │ Cron job     │  (nightly batch:
   │            │                              │ (Airflow /   │   route + manifest)
   │            │                              │  cron)       │
   │            │                              └──────────────┘
```

### 8.2 Step-by-step build

1. **Items + inventory.** Postgres: `items(id, name, price, mrp, category, pack_size)`,
   `dc_inventory(dc_id, item_id, on_hand, reserved)`. Seed a fake DC with ~50 grocery items.
2. **Search.** Postgres full-text. Filter by `on_hand - reserved > 0`.
3. **Slots.** Postgres: `slots(dc_id, start_time, end_time, pick_capacity, used_capacity)`. At
   checkout, atomically reserve:
   ```sql
   UPDATE slots SET used_capacity = used_capacity + 1
   WHERE id=? AND used_capacity < pick_capacity
   RETURNING used_capacity;
   ```
4. **Cart.** Redis hash.
5. **Checkout pipeline.**
   - Validate cart + prices (≤ MRP).
   - Reserve slot capacity (atomic).
   - Reserve inventory (atomic decrement).
   - Mock payment (skip UPI for simplicity).
   - Create order; emit `ORDER_CREATED` to a Redis stream / SQS.
6. **Nightly batch job (cron).** A script that:
   - Pulls all confirmed orders for "tomorrow 7–9 AM."
   - Groups them by zone (fake zone = first 3 chars of pin code).
   - For each group, greedily assigns orders to "routes" of max 10 stops each.
   - Writes `routes` and `route_stops` rows.
7. **DC pick simulator.** A script that:
   - Reads the route.
   - For each item, decrements `on_hand` (true sale).
   - If OOS, picks a substitute (randomly, for demo) and adjusts the order.
   - Marks route as `PICKED`.
8. **Delivery simulator.** A script that "delivers" the route stop by stop, marking each order
   `DELIVERED` with a timestamp.
9. **Frontend.** React: browse items, add to cart, choose slot, place order. A "track order" page
   showing status (`CONFIRMED → PICKED → OUT_FOR_DELIVERY → DELIVERED`).
10. **Bonus: bb Daily.** Add a `subscriptions` table. A nightly cron generates orders from active
    subscriptions.

### 8.3 What you'll learn

- Why **scheduled delivery is more efficient** than instant (batching).
- Why **slot capacity is atomic** (you can't oversell the 7–9 AM window).
- How **batch picking** works (one pick run serves multiple orders).
- Why **FEFO** matters for perishables (vs. FIFO).
- How **VRP** groups orders into routes.
- Why **substitutions** are a core grocery concept (not an afterthought).

### 8.4 Cost for a weekend build

- A laptop running Postgres + Redis + Flask + cron = free.
- Real BigBasket's costs are dominated by **DC real estate + staff + cold chain + fleet** — the
  physical network, not the software.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered          | Why BigBasket chose it                                  |
| ----------------------------------------------- | ------------------------------- | ---------------------------------------------------- |
| **Scheduled-slot delivery (default)**           | Instant on-demand               | Batch efficiency; far better unit economics           |
| **Inventory ownership (DC model)**              | Pure marketplace                | Quality control; perishable handling; margin         |
| **Slot capacity as atomic resource**            | Soft + reconcile                | Never oversell a slot; clean throttle                 |
| **Batch picking (one run, many orders)**        | One pick per order              | Picker efficiency; critical at 4 AM volume            |
| **FEFO for perishables**                         | FIFO                            | Minimise spoilage; expiry-aware                       |
| **Substitution engine**                         | Hard fail on OOS                | Customer retention; grocery reality                   |
| **bb Daily as separate subscription system**     | Fold into main flow             | Different optimisation (fixed routes); nightly batch  |
| **MySQL for inventory + orders**                | NoSQL                           | ACID + atomic decrements + relational                 |
| **Nightly VRP solver**                          | Real-time routing               | Offline solver can be slow + optimal; not on critical path |
| **City-level DC replication**                   | Central national DC             | Perishables + delivery time; city independence        |
| **bb Express layer (instant) on top**           | All-scheduled                   | Compete with quick-commerce; capture instant demand   |

### The deepest trade-off

**Convenience vs. unit economics.** Instant delivery (Swiggy Instamart, Zepto) is customer-delight
but margin-pain. Scheduled delivery (BigBasket core) is margin-healthy but less convenient. BigBasket
straddles both: the **scheduled** business is the profit engine (high AOV, batched routes); the
**bb Express** layer defends against quick-commerce encroachment. The architectural decision to run
*both* models — sharing catalog and customer but with different DC tiers (large DC vs. micro-DC),
different order state machines, and different routing — is a deliberate bet that scheduled delivery
wins on economics while express wins on retention. Tata's acquisition (and capital) is funding the
express expansion; whether both models coexist profitably is the open question.

---

## 10. Common Interview Questions

**Q1: Design BigBasket / an online grocery platform.**
Walk the customer journey (browse → cart → choose slot → checkout → next-day delivery).
Decompose: catalog, search, slot engine, substitution engine, DC/WMS (inventory + batch pick +
FEFO), routing/VRP, last-mile, settlement. Stress the scheduled-slot batching efficiency and the
DC ownership model. Mention bb Express as the instant layer.

**Q2: Why scheduled delivery instead of instant?**
Batching. One driver delivering 15 orders in a 2-hour route is far cheaper than 15 Swiggy-style
trips. Scheduled delivery works for weekly stockups (high AOV); instant serves urgent/top-up
needs. BigBasket does both, but scheduled is the profit engine.

**Q3: How do you handle slot capacity?**
Treat each `(DC, slot)` as a finite resource. Atomic decrement at checkout. When full, the slot
closes. This naturally throttles demand during spikes (e.g., lockdowns) — you can't oversell.

**Q4: How do you prevent overselling inventory?**
Strongly consistent, atomic conditional decrement in the DC inventory DB. SQL `UPDATE ... WHERE
on_hand - reserved >= 1 RETURNING`. For perishables, track per-batch counts with expiry dates.

**Q5: How do you pick efficiently at the DC?**
**Batch picking**: one pick run collects items across multiple orders in a route, sequenced by
aisle for shortest walking path. **FEFO** for perishables (pick earliest-expiry batch first).
Pickers use handheld devices with optimised pick lists.

**Q6: How do you route 50 orders in a slot?**
It's the **Vehicle Routing Problem with Time Windows (VRP-TW)**. Group orders by zone, assign to
vehicles, solve for shortest total distance within the time window. Solved nightly with heuristics
or metaheuristics (OR-Tools, simulated annealing). Not on the critical path (runs at 3 AM).

**Q7: How do substitutions work?**
Customer sets substitution preference per item at order time. At pick time, if OOS, the picker
sees a suggested substitute (same category, similar pack size, equal-or-better brand) and accepts
or rejects. Substitutes are charged at their own price. A high substitution rate (>10%) indicates
inventory planning problems.

**Q8: How does bb Daily (subscription) work?**
Customers subscribe to recurring items (milk daily, bread M/W/F). A nightly cron (10 PM cutoff)
generates tomorrow's orders from active subscriptions, applying customer pauses/modifications.
Orders are routed to **fixed micro-routes** (the same loop every day). Delivered before 7 AM.
Payment: prepaid wallet auto-debit or weekly COD.

**Q9: How do you forecast demand?**
Per DC per SKU per day. Grocery demand has strong weekly cycles (weekend spikes) and festival
spikes. Forecast drives procurement, DC staffing, and slot capacity. ML models (time-series +
features) run daily.

**Q10: How do you handle perishables + cold chain?**
Cold storage at DC + cold-chain transport. Batch tracking with expiry dates; FEFO picking.
Spoilage monitored (target <2–3% shrinkage). Spoilage alerts + dynamic markdown near expiry.

**Q11: Your DC goes down the night before a slot. What happens?**
Failover to a backup DC if available; else, rebook affected orders to the next slot with
apology + credit. Slot capacity is per-DC, so a DC failure only affects that DC's slots. DR for
orders/payments; the DC itself is a physical failure domain.

**Q12: How do you handle Diwali / lockdown demand spikes?**
Slot capacity is the natural throttle — you show "no slots today" instead of accepting orders you
can't fulfill. Pre-stock DCs for forecast demand. Expand pick/delivery capacity (more shifts).
bb Express may pause in extreme spikes to protect the scheduled core.

---

## Further reading

- BigBasket engineering talks (RubyConf India, AWS events, Rootcon) — on their stack evolution.
- Tata Digital / BigBasket integration press — for the strategic context.
- Vehicle Routing Problem literature (OR-Tools docs) — for routing solvers.
- FEFO / perishable inventory management — supply-chain texts.
- NPCI UPI specs — for the payment rail.
- Compare with `flipkart.md` (e-commerce logistics) and `swiggy.md` (instant delivery) for the
  contrast in delivery models.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures, quarterly reports, and
engineering talks.*

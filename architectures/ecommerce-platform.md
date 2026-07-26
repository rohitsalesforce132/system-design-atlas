# E-Commerce Platform — Sample Architecture

> **Audience:** A developer who wants to understand how to design an Amazon/Flipkart-style e-commerce backend from scratch. Plain English, real numbers, ASCII diagrams, basics-first — analogies before advanced concepts.

---

## 1. Problem Statement & Requirements

### 1.1 What are we building?

An online marketplace where users can browse a product catalog, search for products, add items to a cart, check out, pay, and track orders. Sellers (or the platform itself) manage inventory and fulfill orders. Think Amazon, Flipkart, Myntra, or Shopify Plus.

**The analogy:** Imagine a giant physical department store. There's a **showroom** (product catalog, search, images), a **shopping basket** you carry around (the cart), a **billing counter** (checkout + payment), a **warehouse** (inventory), and a **shipping desk** (order tracking). Each of these is a separate team with its own tools. In software, each becomes a separate service with its own database. The customer experiences all of them as one continuous flow — but underneath, they are decoupled so a warehouse fire doesn't burn down the showroom.

### 1.2 Functional Requirements

| # | Requirement | Priority |
|---|---|---|
| F1 | Browse product catalog by category, sub-category, filters | P0 |
| F2 | Full-text search with typo tolerance, ranking, facets | P0 |
| F3 | View product detail page (images, price, variants, reviews) | P0 |
| F4 | Add/remove/update quantity in cart | P0 |
| F5 | Checkout: address, coupon, payment | P0 |
| F6 | Inventory reservation during checkout (don't oversell) | P0 |
| F7 | Payment processing via gateway (UPI, cards, wallets) | P0 |
| F8 | Order placement, confirmation, history | P0 |
| F9 | Order status tracking (packed, shipped, delivered) | P0 |
| F10 | Seller portal: add products, update stock, fulfill orders | P1 |
| F11 | Reviews and ratings | P1 |
| F12 | Recommendations ("customers also bought") | P1 |
| F13 | Wishlist | P2 |
| F14 | Returns / refunds | P1 |

### 1.3 Non-Functional Requirements

| Attribute | Target | Why |
|---|---|---|
| Availability | 99.99% (≈52 min downtime/year) | Downtime = lost revenue; checkout failure is a brand-damaging event |
| Read latency (catalog/search/PDP) | p99 < 200 ms | Slow pages kill conversion (Amazon: 100ms latency = 1% sales loss) |
| Write latency (cart, order) | p99 < 500 ms | Acceptable for user-initiated actions |
| Read:Write ratio | ~100:1 (browse-heavy) | Most users browse; few buy |
| Consistency | Strong for inventory & payment; eventual for catalog search index | You cannot oversell; you can show a stale product description briefly |
| Scalability | Handle 10× flash-sale bursts (Big Billion Days) | Flash sales are the design driver |
| Durability | No payment or order is ever lost | Payments are money — treat them like bank transactions |
| Security | PCI-DSS scope isolated to payment service; PII encrypted at rest | Regulatory + trust |

### 1.4 Out of scope (for this design)

- Logistics / last-mile delivery fleet (talks to a 3PL API).
- Warehouse management system (WMS) internals.
- Marketing campaigns, email/SMS blasts (separate notification system).
- Seller onboarding KYC.

---

## 2. Capacity Estimation

Let's size for a **mid-to-large marketplace**: 50 million monthly active users (MAU), ~2% of whom buy each day.

### 2.1 Traffic

| Metric | Assumption | Math | Result |
|---|---|---|---|
| Daily active users (DAU) | 30% of MAU | 50M × 0.30 | **15M DAU** |
| Sessions/user/day | 1.5 | 15M × 1.5 | 22.5M sessions/day |
| Page views/session | 8 | 22.5M × 8 | **180M page views/day** |
| API calls/page view | ~6 (REST + image fetch) | 180M × 6 | ~1.08B calls/day |
| Avg QPS | 1.08B / 86400s | | **~12,500 QPS (avg)** |
| Peak QPS (3–5× avg) | 12,500 × 4 | | **~50,000 QPS (peak)** |
| Flash sale QPS | 10–20× for product page | | **~250,000 QPS (flash sale)** |

### 2.2 Orders / Cart

| Metric | Math | Result |
|---|---|---|
| Daily buyers | 15M × 2% | **300,000 orders/day** |
| Peak orders/sec (noon spike) | 300,000 / (6h × 3600) × 5 | **~70 orders/sec peak** |
| Cart writes/sec | 5× order rate | ~350 cart updates/sec |
| Flash sale order spike | 100× | ~7,000 orders/sec (Big Billion Days style) |

### 2.3 Storage

| Data | Per-unit size | Count/year | Total |
|---|---|---|---|
| Products | 50 KB (metadata + 5 images @ 10 KB thumb) | 100M SKUs | **5 TB** |
| Original product images | 500 KB × 5 per product | 500M images | **250 TB** |
| Orders | 2 KB (header + 3 line items) | 300K × 365 = 110M | **0.22 TB** |
| Order history over 5 yr | 2 KB × 550M | | **~1.1 TB** |
| Users | 1 KB | 50M | 0.05 TB |
| Reviews | 1 KB | 50M | 0.05 TB |
| Search index | ~2× catalog text | | ~5 TB |
| **Total hot + warm** | | | **~260 TB** |

### 2.4 Bandwidth

| Source | Math | Bandwidth |
|---|---|---|
| Images (CDN-served) | 180M PV × 2 images × 50 KB | **1.8 PB/day** → ~**20 Gbps avg** |
| Peak image bandwidth | 4× avg | **~80 Gbps** |
| API JSON | 180M × 6 × 1 KB | ~1 TB/day → ~100 Mbps |

### 2.5 Compute

- **Catalog/Search/PDP services:** CPU-light (read from cache). ~30 pods × 4 vCPU at peak.
- **Checkout/Order:** CPU-medium (validation, payment calls). ~20 pods × 4 vCPU.
- **Image processing (upload):** Bursty; autoscaled workers.
- **Search indexing:** Continuous; ~10 vCPU steady, spikes on catalog refresh.

### 2.6 Cache hit ratio needed

To hit 200 ms p99 reads at 50k QPS, we need **>95% cache hit ratio** for catalog and PDP — only 5% of reads hit the DB. This drives the Redis design in §4.

---

## 3. High-Level Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │                 USERS                        │
                          │   Web · iOS · Android · Seller Portal        │
                          └─────────────────────────────────────────────┘
                                            │  HTTPS
                                            ▼
                          ┌─────────────────────────────────────────────┐
                          │   CDN  (CloudFront / Cloudflare)             │
                          │   - static assets (JS, CSS, product images)  │
                          │   - 90%+ of bytes served from edge           │
                          └─────────────────────────────────────────────┘
                                            │  (cache miss only)
                                            ▼
                          ┌─────────────────────────────────────────────┐
                          │   DNS  (GeoDNS / Route53)                    │
                          │   - route user to nearest region             │
                          └─────────────────────────────────────────────┘
                                            │
                                            ▼
                          ┌─────────────────────────────────────────────┐
                          │   Load Balancer  (ALB / Nginx)               │
                          │   - TLS termination, L7 routing              │
                          └─────────────────────────────────────────────┘
                                            │
                                            ▼
                          ┌─────────────────────────────────────────────┐
                          │   API Gateway  (Kong / Envoy / AWS API GW)   │
                          │   - auth (JWT verify)                        │
                          │   - rate limiting (per user / per IP)        │
                          │   - request routing to services              │
                          │   - idempotency key check                    │
                          └─────────────────────────────────────────────┘
                                            │
              ┌──────────────┬──────────────┼──────────────┬──────────────┐
              ▼              ▼              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Catalog  │  │ Search   │  │  Cart    │  │ Checkout │  │  Order   │
        │ Service  │  │ Service  │  │ Service  │  │ Service  │  │ Service  │
        │  (Go)    │  │ (Node)   │  │ (Go)     │  │ (Java)   │  │ (Java)   │
        └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
             │             │             │             │             │
             ▼             ▼             ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
        │PostgreSQL│  │ Elastic  │  │  Redis   │  │PostgreSQL│  │PostgreSQL│
        │ (catalog)│  │  search  │  │ (cart)   │  │(checkout)│  │ (orders) │
        │ cluster  │  │  cluster │  │ cluster  │  │ cluster  │  │ cluster  │
        └────┬─────┘  └──────────┘  └──────────┘  └────┬─────┘  └────┬─────┘
             │                                          │             │
             │             ┌────────────────────────────┘             │
             │             │                                          │
             │             ▼                                          │
             │       ┌──────────┐        ┌─────────────────────────────┘
             │       │Payment   │        ▼
             │       │ Service  │   ┌──────────┐
             │       │ (PCI     │   │ Kafka    │  ← events: order.created,
             │       │  scope)  │   │ cluster  │     inventory.updated,
             │       └────┬─────┘   └────┬─────┘     payment.captured
             │            │             │
             │            ▼             ├──▶ Inventory Worker (decrement stock)
             │       ┌──────────┐       ├──▶ Notification Worker (email/SMS)
             │       │ Payment  │       ├──▶ Analytics (ClickHouse)
             │       │ Gateway  │       ├──▶ Search Indexer (push to ES)
             │       │ (Stripe/ │       └──▶ Recommendation (feature store)
             │       │  Razorpay│
             │       │  /UPI)   │
             │       └──────────┘
             ▼
        ┌──────────┐
        │   S3 /   │  ← original product images, invoices, ETL exports
        │  Object  │
        │  Store   │
        └──────────┘
```

### 3.1 The mental model: "showroom, basket, billing, warehouse"

- **Catalog + Search + CDN** = the showroom. Read-heavy, cacheable, eventually consistent.
- **Cart** = the basket. Small, personal, ephemeral (lives in Redis).
- **Checkout + Payment** = the billing counter. Strong consistency, money-critical.
- **Order + Inventory** = the warehouse. Durable, transactional, the system of record.

The decoupling principle: the showroom can be down for maintenance without stopping the warehouse from shipping orders already placed. Conversely, a warehouse inventory recount shouldn't block browsing.

---

## 4. Component Selection

### 4.1 API Gateway — Kong (or Envoy)

**Why:** Kong gives us auth plugin (JWT), rate limiting, request transformation, and observability in one place. Centralizing cross-cutting concerns means each microservice doesn't reimplement them.
**Alternatives considered:**
- *AWS API Gateway* — managed, zero-ops, but vendor lock-in and per-request pricing gets expensive at 50k QPS.
- *Nginx + Lua* — powerful but you're maintaining custom Lua code; harder to hire for.
- *Spring Cloud Gateway* — fine if your stack is fully JVM; less universal.

### 4.2 Service language: Go for hot paths, Java for transactional

- **Catalog, Cart** in **Go** — these are I/O-bound, latency-sensitive read paths; Go's low memory footprint and great concurrency suit them.
- **Checkout, Order** in **Java (Spring Boot)** — rich ecosystem for transactions, idempotency libraries, and the team has more experience with complex business logic in Java.
- **Search Service** in **Node.js** — thin shim over Elasticsearch, fast to iterate.

**Alternatives:** All-Java (consistent hiring, but heavier memory); all-Go (leaner, but fewer libraries for payment orchestration); Rust (great perf, but hiring cost too high for this scope).

### 4.3 PostgreSQL for catalog, checkout, orders

**Why:**
- ACID transactions are mandatory for orders & payments (you can't have a half-committed order).
- Rich query semantics for product attributes, JOINs for order details.
- Mature, well-understood operational story.

**Alternatives:**
- *MySQL* — equally valid; choice often comes down to team familiarity. We pick Postgres for JSONB (flexible product attributes) and richer indexing.
- *MongoDB* — tempting for the flexible product schema, but lack of multi-document transactions (historically) and JOINs make order/checkout harder. We use Postgres JSONB instead to get flexibility *and* ACID.
- *DynamoDB* — great for the Cart, but ad-hoc queries on catalog are painful without a secondary index service.

### 4.4 Redis for cart, session, hot cache

**Why:** Sub-millisecond reads; native data structures (hashes for cart line items); TTL for session expiry; atomic ops for inventory reservation counters.
**Alternatives:** Memcached (no data structures, no persistence); DynamoDB (higher latency, more cost at our access pattern).

### 4.5 Elasticsearch for search

**Why:** Full-text search with typo tolerance (Levenshtein), ranking (BM25), facets/aggregations (brand, price range), and horizontal scaling. The product catalog is a textbook ES use case.
**Alternatives:** Algolia (SaaS, excellent relevance, but pricing at 100M docs is steep); Postgres full-text search (works for small scale, lacks facets and relevance tuning); OpenSearch (fork of ES, fully open-source — equally valid).

### 4.6 Kafka for event streaming

**Why:** Decouples services via events (`order.created`, `inventory.updated`, `payment.captured`). Producers don't know or care about consumers. Replay capability for bug fixes. Backbone for the search indexer and analytics pipeline.
**Alternatives:** RabbitMQ (great for task queues, weaker for event replay and analytics fan-out); SQS (simple, but no replay, no streaming semantics); Pulsar (powerful, but smaller ecosystem).

### 4.7 S3 / Object Storage for images

**Why:** Infinite scale, 11×9 durability, cheap, integrates with CDN.
**Alternatives:** Self-hosted MinIO (more ops); block storage (too expensive, not designed for this access pattern).

### 4.8 CDN (CloudFront / Cloudflare)

**Why:** 80%+ of e-commerce bandwidth is images and static assets. CDN caches at the edge, cutting origin load dramatically and bringing content physically closer to users.
**Alternatives:** Akamai (enterprise, expensive); self-hosted edge (prohibitive cost).

### 4.9 Payment gateway integration

**Why:** Never store raw card numbers (PCI-DSS scope). Delegate to Stripe/Razorpay/Adyen; store only tokens and last-4. Reduces compliance burden to SAQ-A.

---

## 5. Database Schema Design

We use **database-per-service**: each service owns its schema and no other service reads its tables directly (they go through the service API or via events).

### 5.1 Catalog (PostgreSQL — `catalog` db)

```sql
CREATE TABLE categories (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(128) NOT NULL,
    parent_id    BIGINT REFERENCES categories(id),  -- self-ref tree
    slug         VARCHAR(128) UNIQUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_categories_parent ON categories(parent_id);

CREATE TABLE products (
    id            BIGSERIAL PRIMARY KEY,
    seller_id     BIGINT NOT NULL,
    category_id   BIGINT REFERENCES categories(id),
    title         VARCHAR(256) NOT NULL,
    description   TEXT,
    brand         VARCHAR(128),
    attributes    JSONB,                 -- flexible: color, size, weight...
    base_price    NUMERIC(10,2) NOT NULL,
    currency      CHAR(3) DEFAULT 'USD',
    status        VARCHAR(16) DEFAULT 'active',  -- active|inactive|deleted
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_products_category  ON products(category_id) WHERE status='active';
CREATE INDEX idx_products_seller    ON products(seller_id);
CREATE INDEX idx_products_attr_gin  ON products USING gin (attributes);  -- JSONB queries

CREATE TABLE product_variants (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT REFERENCES products(id),
    sku          VARCHAR(64) UNIQUE,
    variant_attrs JSONB,                -- {size: "M", color: "blue"}
    price        NUMERIC(10,2),
    image_refs   TEXT[]                 -- S3 keys
);
CREATE INDEX idx_variants_product ON product_variants(product_id);
```

**Indexes explained:** `idx_products_category` speeds category browse; the GIN index on `attributes` lets us filter on any JSONB key (e.g., "color = red") without pre-defining columns.

### 5.2 Inventory (PostgreSQL — `inventory` db, separate from catalog)

```sql
CREATE TABLE inventory (
    sku_id        BIGINT PRIMARY KEY,
    available_qty INT NOT NULL DEFAULT 0,
    reserved_qty  INT NOT NULL DEFAULT 0,   -- held during checkout
    version       BIGINT NOT NULL DEFAULT 0, -- optimistic concurrency
    updated_at    TIMESTAMPTZ DEFAULT now(),
    CHECK (available_qty >= 0)
);

CREATE TABLE inventory_reservations (
    id            BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL,
    sku_id        BIGINT REFERENCES inventory(sku_id),
    qty           INT NOT NULL,
    status        VARCHAR(16),  -- held | committed | released
    expires_at    TIMESTAMPTZ,   -- TTL: auto-release if checkout abandoned
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_resv_order  ON inventory_reservations(order_id);
CREATE INDEX idx_resv_expires ON inventory_reservations(expires_at) WHERE status='held';
```

**Why separate schema:** Inventory has a vastly different write profile (high-concurrency updates) than catalog (mostly reads). Separating lets us tune (and scale) them independently. The `version` column enables optimistic locking to avoid oversell (see §7).

### 5.3 Cart (Redis — no SQL, hash per user)

```
Key:   cart:{user_id}
Type:  Hash
Fields: sku_id -> {"qty":2, "price":19.99, "title":"..."}
TTL:   30 days (sliding, refreshed on activity)
```

Why Redis not Postgres for cart: carts are read/written on every page view ("add to cart" button shows count), need sub-ms latency, and are ephemeral (losing a cart on a Redis flush is annoying but not catastrophic — user re-adds). Postgres would buckle under the write load.

### 5.4 Orders (PostgreSQL — `orders` db)

```sql
CREATE TABLE orders (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    status          VARCHAR(16) NOT NULL,  -- pending|paid|fulfilled|cancelled
    subtotal        NUMERIC(10,2),
    discount        NUMERIC(10,2) DEFAULT 0,
    shipping        NUMERIC(10,2) DEFAULT 0,
    tax             NUMERIC(10,2) DEFAULT 0,
    total           NUMERIC(10,2) NOT NULL,
    currency        CHAR(3),
    shipping_addr   JSONB,                  -- snapshot at order time
    payment_id      VARCHAR(64),            -- gateway reference
    idempotency_key VARCHAR(64) UNIQUE,     -- prevents double-placement
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_orders_user  ON orders(user_id, created_at DESC);

CREATE TABLE order_items (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT REFERENCES orders(id),
    sku_id      BIGINT NOT NULL,
    product_id  BIGINT,                  -- snapshot for historical display
    title       VARCHAR(256),            -- snapshot (in case product renames)
    unit_price  NUMERIC(10,2),
    qty         INT NOT NULL,
    line_total  NUMERIC(10,2)
);
CREATE INDEX idx_items_order ON order_items(order_id);

CREATE TABLE order_events (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT REFERENCES orders(id),
    event_type  VARCHAR(32),  -- created|paid|packed|shipped|delivered|cancelled
    payload     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_events_order ON order_events(order_id, created_at);
```

**Key decision:** `order_items` snapshots `title` and `unit_price` at purchase time. If a seller later renames a product or changes its price, past orders still show what the user actually bought. This is a classic e-commerce correctness pattern.

### 5.5 Search Index (Elasticsearch)

```json
{
  "product_id": 12345,
  "title": "Apple iPhone 15 Pro",
  "brand": "Apple",
  "category_path": ["Electronics", "Phones", "Smartphones"],
  "attributes": { "color": "Titanium", "storage": "256GB" },
  "price": 1099.00,
  "in_stock": true,
  "avg_rating": 4.7,
  "review_count": 1832,
  "image_url": "https://cdn.../iphone15.jpg",
  "seller_id": 42,
  "updated_at": "2026-07-20T..."
}
```

Index refreshed every few seconds via the Kafka `inventory.updated`/`catalog.updated` events.

---

## 6. API Design

REST + JSON for client-facing APIs; gRPC for inter-service calls (typed, fast).

### 6.1 Catalog

```
GET /v1/products?category=smartphones&sort=price_asc&page=1&size=24
→ 200 OK
{
  "products": [ { "id":1, "title":"...", "price":1099.00, "image":"...", "rating":4.7 } ],
  "page": 1, "size": 24, "total": 1832
}

GET /v1/products/{id}
→ 200 OK
{
  "id": 1, "title": "...", "description": "...", "variants": [...],
  "images": [...], "attributes": {...}, "seller": {...}, "reviews": {...}
}
```

### 6.2 Search

```
GET /v1/search?q=iphone+15&filters={"brand":["Apple"],"price_max":1500}&sort=relevance
→ 200 OK
{
  "hits": [ { "product_id":1, "title":"...", "price":1099.00, "_score": 18.4 } ],
  "facets": {
    "brand":    { "Apple": 42, "Samsung": 18 },
    "price":    { "0-500": 3, "500-1000": 12, "1000-1500": 27 }
  },
  "total": 42
}
```

### 6.3 Cart

```
POST   /v1/cart/items            { "sku_id": 99, "qty": 2 }
GET    /v1/cart                  → { "items": [...], "subtotal": 39.98 }
PATCH  /v1/cart/items/{sku_id}   { "qty": 3 }
DELETE /v1/cart/items/{sku_id}
```

### 6.4 Checkout & Order

```
POST /v1/checkout/quote
  Body: { "items":[...], "address_id":1, "coupon":"SAVE10" }
→ 200 OK
  {
    "quote_id": "q_abc123",
    "expires_at": "2026-07-26T...",
    "line_items": [...],
    "subtotal": 2198.00, "discount": 219.80, "shipping": 0, "total": 1978.20,
    "available_payment_methods": ["upi","card","wallet"]
  }
```

The quote *reserves* inventory for ~10 minutes (the `expires_at`). This is the critical anti-oversell mechanism (see §7).

```
POST /v1/orders
  Headers: Idempotency-Key: <uuid>          ← REQUIRED, prevents double placement
  Body: { "quote_id":"q_abc123", "payment_method":"upi" }
→ 201 Created
  { "order_id": 88001, "status":"pending_payment", "payment": { "gateway":"...", "ref":"..." } }

GET  /v1/orders/{id}
GET  /v1/orders?status=paid&page=1
```

### 6.5 Payment webhook (gateway → us)

```
POST /v1/payments/webhook      (from Stripe/Razorpay, signature-verified)
  { "event":"payment.captured", "ref":"...", "order_id":88001 }
→ 200 OK
```

The webhook is **idempotent** — replaying it must not double-fulfill the order. We dedupe on `(order_id, event_id)`.

---

## 7. Step-by-Step Request Flow — "User Buys an iPhone"

```
 User           API GW        Catalog       Search       Cart       Checkout    Inventory    Payment    Order       Kafka
 ────           ──────        ───────       ──────       ────       ────────    ──────────    ────────    ─────       ────
  │
  │─ browse ──────────────────▶│
  │                            │─ (CDN serves PDP from edge cache) ─▶
  │◀─── product page (cached) ─│
  │
  │─ search "iphone 15" ───────▶│
  │                            │────────── query ES ────────────────▶│
  │◀────────── results ──────────────────────────────────────────────│
  │
  │─ add to cart ──────────────▶│
  │                            │──────── HSET cart:{u} ────────────────────────────▶│
  │◀────── cart updated ────────│
  │
  │─ checkout ─────────────────▶│
  │                            │──────────────────────────────────── create quote ─▶│
  │                            │                                                        │─ BEGIN TXN
  │                            │                                                        │- check & reserve stock
  │                            │                                                        │  (UPDATE inventory
  │                            │                                                        │   SET reserved = reserved + qty
  │                            │                                                        │   WHERE available - reserved >= qty)
  │                            │                                                        │- INSERT inventory_reservations (status=held, expires=+10min)
  │                            │                                                        │- COMMIT
  │                            │◀────────────────────────────── quote_id ─────────────│
  │◀────── quote (10-min hold) ─│
  │
  │─ pay (UPI) ────────────────▶│
  │                            │──────── create order (idempotent) ──────────────────────────────────────▶│
  │                            │                                                        │                     │- INSERT orders (status=pending)
  │                            │                                                        │                     │- call payment gateway
  │                            │                                                        │                     │   ─ create charge ─▶│
  │                            │                                                        │                     │◀────── auth url ────│
  │◀────── redirect to UPI ─────│
  │
  │─ UPI approval (off-app) ────│
  │                            │
  │              (async webhook)│◀──── payment.captured ────────────────────────────────────────────────│
  │                            │                                                        │                     │- verify signature
  │                            │                                                        │                     │- UPDATE orders SET status='paid'
  │                            │                                                        │                     │- commit reservation (held→committed)
  │                            │                                                        │                     │- produce order.created ──────────▶│
  │                            │                                                        │                     │                                  ├──▶ Inventory Worker: decrement available
  │                            │                                                        │                     │                                  ├──▶ Notification: email "Order confirmed"
  │                            │                                                        │                     │                                  ├──▶ Analytics: track conversion
  │                            │                                                        │                     │                                  └──▶ Search: update in_stock flag
  │◀──── push notification "Order placed!" ─────────────────────────────────────────────────────────────│
```

### 7.1 The anti-oversell trick (most important part of the design)

During the `quote` step we run, inside a single Postgres transaction:

```sql
BEGIN;
  SELECT available_qty, reserved_qty FROM inventory
    WHERE sku_id = 99 FOR UPDATE;          -- row lock
  -- application checks: available - reserved >= requested_qty
  UPDATE inventory
    SET reserved_qty = reserved_qty + 2,
        version      = version + 1
    WHERE sku_id = 99;
  INSERT INTO inventory_reservations(...);  -- held, expires in 10 min
COMMIT;
```

`FOR UPDATE` locks the row so concurrent checkouts serialize on that SKU. Combined with the `version` column, this is **pessimistic + optimistic locking** — bulletproof for oversell prevention. The trade-off: under a flash sale, this row becomes a hot lock; we mitigate with the techniques in §8.

### 7.2 Why 10-minute hold?

If the user abandons checkout, the hold auto-expires and stock returns to the pool. A cron/sweeper job scans `inventory_reservations WHERE status='held' AND expires_at < now()` and releases them. Without this, abandoned carts would permanently drain inventory.

---

## 8. Scaling Strategy

### 8.1 Read scaling (Catalog, Search, PDP) — the easy part

| Bottleneck | Solution |
|---|---|
| 50k QPS to catalog DB | Redis cache in front of Postgres. PDP and category pages cached 60s. **>95% hit rate** keeps DB load <2.5k QPS. |
| Search cluster at 250k QPS (flash sale) | Elasticsearch scales horizontally — add data nodes. Query cache + replica shards for read throughput. |
| Image bandwidth (80 Gbps) | CDN absorbs 90%+. Origin only sees cache misses. |
| Stale cache after price change | Cache invalidation on `catalog.updated` event (Kafka → cache-buster worker). Acceptable staleness ≤60s for non-inventory fields. |

### 8.2 Write scaling — the hard part

**Cart writes:** Redis cluster, sharded by `user_id`. Redis handles 100k+ ops/sec per shard; trivially scales.

**Order writes:** Postgres primary with read replicas for `GET /orders`. Sharding becomes necessary around ~5k writes/sec. We shard `orders` by `user_id` (so a user's full history lives on one shard). Cross-shard queries (analytics) go through Kafka → ClickHouse instead of hitting the OLTP shards.

**Inventory writes (flash sale hot spot):** This is the classic e-commerce scaling problem — 10,000 users trying to buy 100 iPhones simultaneously all hit the *same* SKU row. Solutions, in escalating complexity:
1. **Row-level locking** (§7) — correct but serializes; ~1k txns/sec max.
2. **Redis reservation counters** — keep `available_qty` in Redis, decrement atomically with `DECRBY` + Lua check. Drain to Postgres asynchronously. Handles 50k+ ops/sec per key.
3. **Pre-deduction** — at sale start, move all stock into Redis. Postgres inventory is updated only after orders finalize. Trade-off: harder reconciliation if Redis crashes.
4. **Queue-based checkout** — flash-sale items go through a virtual queue (Kafka or a token-bucket limiter). Users wait in line; only N checkouts/sec proceed. This is how real flash sales survive.

### 8.3 Payment scaling

- Payment gateway calls are the slowest hop (200ms–2s). Don't block the request thread — use async/non-blocking HTTP clients.
- Webhooks are idempotent and can be retried safely.
- Stripe/Razorpay handle their own scale; our job is to not become the bottleneck on our side.

### 8.4 Kafka scaling

- Partition by `order_id` for order events (preserves per-order ordering).
- Partition by `sku_id` for inventory events (preserves per-SKU ordering).
- Consumer groups per downstream service; scale consumers per partition.

### 8.5 Database scaling progression

```
1 box Postgres  →  primary + read replicas  →  vertical scale up  →  horizontal sharding
                                                                         │
                                                                         └─ by user_id (orders, cart)
                                                                            by sku_id (inventory)
                                                                            by category_id (catalog)
```

### 8.6 Multi-region

For 99.99% availability, deploy in 2+ regions:
- **Read-traffic regions:** CDN + read replicas in each region.
- **Write traffic:** single writer region (or active-active with conflict resolution for cart/orders). Inventory is kept single-region to avoid cross-region locking; replicas are read-only.

---

## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---|---|---|
| **Payment gateway down** | Users can't pay | Graceful error + retry; offer alternative gateway; queue webhook processing for late capture |
| **Cart Redis crash** | Users lose in-flight cart | AOF persistence + replica failover; carts reconstructable from last-known-state log; worst case user re-adds |
| **Inventory DB primary down** | Can't reserve stock → checkout fails | HA Postgres (sync replica + automatic failover); failover <30s; checkout shows "retry" not "error" |
| **Elasticsearch cluster degraded** | Search slow or partial | Query-timeout + fallback to Postgres `ILIKE` search (lower quality, but functional); degraded-search banner |
| **Kafka broker failure** | Events delayed | Replication factor 3; consumers offset-rewind on recovery; at-least-once + idempotent consumers |
| **CDN origin failure** | Images 404 | CDN serves stale content (stale-while-revalidate); origin health checks + circuit breaker |
| **Flash sale hot row** | Checkout timeouts | Queue-based checkout (§8.2.4); pre-warm Redis counters; autoscale order service |
| **Network partition (split-brain)** | Inconsistent state | Postgres synchronous replication + fencing; never accept writes on a partitioned minority |
| **Double-click "Place Order"** | Duplicate orders | Idempotency-Key header (§6.4) — second request returns the first order's result, not a new order |
| **Webhook replay by attacker** | Double fulfillment | Webhook signature verification + idempotent handler keyed on gateway event ID |
| **Clock skew across services** | Reservation expiry bugs | Use DB `now()` for timestamps, not app-server clocks; NTP on all hosts |

### 9.1 The "money never disappears" guarantee

Every payment has exactly one of these terminal states: **captured** or **failed/refunded**. We never leave an order in `pending_payment` forever:
- A **reconciliation job** runs every 5 minutes: queries the payment gateway for `pending` orders older than 15 min, and force-updates status based on gateway truth.
- If gateway says "captured" but our DB still says "pending", we update to `paid` and emit the order events (reconciliation = self-healing).

---

## 10. Trade-off Analysis

### 10.1 Microservices vs. Monolith

- **Choice:** Microservices (catalog, cart, checkout, order, inventory, payment).
- **Benefit:** Independent scaling (catalog scales for reads, checkout for writes); fault isolation (a search bug doesn't break checkout); team autonomy.
- **Cost:** Operational complexity (6+ deployables), distributed transactions (sagas), harder debugging, network latency between calls. At our scale the benefit wins; a small startup would choose a monolith first and split later.

### 10.2 Strong vs. eventual consistency for inventory

- **Choice:** Strong consistency (Postgres + locking) for the reserve/commit cycle.
- **Why:** Overselling is a direct revenue loss + brand damage. You cannot tell 200 users "you bought it" when only 100 exist.
- **Cost:** Lower throughput on hot SKUs; we pay for correctness with queueing during flash sales.

### 10.3 Redis cart vs. Postgres cart

- **Choice:** Redis.
- **Why:** Latency and write volume. Postgres would work functionally but cost 10× the latency and require aggressive sharding.
- **Cost:** Cart durability is weaker (Redis AOF is good but not ACID). We accept this because a cart is user-reconstructable; it's not a system of record.

### 10.4 Denormalized order_items (snapshotting price/title)

- **Choice:** Snapshot at order time.
- **Why:** An order is a historical record — it must reflect what was paid, not current catalog state. If we JOINed live product data, a price change would corrupt past orders.
- **Cost:** Storage grows faster; no automatic link if a product is renamed. Worth it.

### 10.5 Kafka vs. synchronous service calls

- **Choice:** Kafka for post-order fanout (inventory decrement, notifications, analytics, search index).
- **Why:** Producers (order service) don't block on consumers. If the email service is slow, orders still succeed.
- **Cost:** Eventually consistent (email may arrive seconds later); consumers must be idempotent; debugging requires distributed tracing.

### 10.6 Elasticsearch vs. Postgres full-text search

- **Choice:** Elasticsearch.
- **Why:** Faceted search (brand, price range), typo tolerance, relevance tuning (BM25), and scale — none of which Postgres FTS does well.
- **Cost:** Operational overhead (separate cluster, indexing pipeline); eventual consistency between catalog DB and search index (seconds of staleness).

### 10.7 Single-region writes vs. multi-region active-active

- **Choice:** Single-region writes (with multi-region reads).
- **Why:** Inventory and payment correctness depend on a single serialization point. Multi-region active-active would require complex conflict resolution (CRDTs or last-write-wins) that risks oversell.
- **Cost:** Higher write latency for users far from the primary region; the primary region is a single point of failure mitigated by HA failover (not true multi-region survival).

### 10.8 Idempotency everywhere

- **Choice:** Every state-changing API requires an `Idempotency-Key`. Payment webhooks are idempotent on event ID. Consumers dedupe on event+offset.
- **Why:** Networks retry. Users double-click. Kafka delivers at-least-once. Without idempotency, every one of these creates duplicates (double charges, double orders).
- **Cost:** Extra storage for the key index; slightly more complex code. Non-negotiable for money paths.

---

## Appendix: Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| CDN | CloudFront / Cloudflare | Image + static asset delivery at the edge |
| Load Balancer | AWS ALB / Nginx | L7 routing, TLS termination |
| API Gateway | Kong | Auth, rate limit, routing, observability |
| Catalog Service | Go + PostgreSQL | Read-heavy, low-latency |
| Search Service | Node.js + Elasticsearch | Faceted full-text search |
| Cart Service | Go + Redis | Sub-ms writes, ephemeral |
| Checkout Service | Java/Spring + PostgreSQL | Transactional, idempotent |
| Order Service | Java/Spring + PostgreSQL | System of record |
| Inventory Service | Go + PostgreSQL + Redis | Strong consistency + hot-path counters |
| Payment Service | Java (PCI-scoped) + Gateway | Tokenized, no raw card data |
| Event Bus | Kafka | Decoupling, replay, analytics |
| Object Storage | S3 | Product images, invoices |
| Analytics | ClickHouse | OLAP on Kafka event stream |
| Orchestration | Kubernetes | Autoscaling, rolling deploys |
| Observability | Prometheus + Grafana + Jaeger | Metrics, dashboards, distributed tracing |

---

*End of E-Commerce Platform architecture.*

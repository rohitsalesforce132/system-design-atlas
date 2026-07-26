# Airbnb — System Design

> **Reader's note:** This is a deep, standalone walkthrough. Read top to bottom and you will understand how Airbnb works end-to-end — the search pipeline, the booking transaction, the data model, the trade-offs, and how to build a simplified clone yourself. No buzzwords without explanation.

---

## 1. Overview & Scale Numbers

Airbnb is a **two-sided marketplace** connecting hosts (who list properties) with guests (who book stays). It's deceptively complex because it must serve two different user types with opposite needs, handle real money and real-world trust, and search across a massive geographic inventory.

### Why is this hard?

Four hard problems combine:

1. **Two-sided marketplace.** You need both hosts and guests; neither is useful without the other. Liquidity (enough listings in a market to attract guests, enough guests to attract hosts) is the core business problem.
2. **Search at geographic scale.** "Show me all available properties in Paris for these dates, ranked by relevance, with prices, in 200ms." The inventory is huge and availability changes every second.
3. **Transactional integrity.** Money moves between parties, calendars must not double-book, and a failed booking is a disaster. This demands strong consistency in specific places.
4. **Trust & safety.** Reviews, identity verification, host guarantees, payments risk. The product must make strangers comfortable staying in each other's homes.

### Real-world scale (publicly reported / industry estimates)

| Metric | Approximate value |
|---|---|
| Active listings | ~7–8M worldwide |
| Countries | 220+ |
| Guests to date | 1.5B+ cumulative |
| Hosts | ~4M+ |
| Bookings per night (peak) | millions |
| Stay reservations per year | 400M+ |
| Cities | 100,000+ |
| Languages supported | 60+ |
| Currencies | 70+ |
| Photos stored | 100M+ (high-res) |
| Search queries/day | 100M+ |

**Storage math (back-of-envelope):**

- 7M listings × avg 50 fields of metadata ≈ manageable relational scale.
- 7M listings × ~30 high-res photos each × ~3MB = ~600 TB of image data. **Images dominate storage** by ~1000:1.
- Reviews: hundreds of millions of rows. Append-only, time-ordered.

**Latency targets:**

- **Search results:** < 500 ms at p99 (geographic + date filtering + ranking).
- **Booking confirmation:** < 2 seconds (the transaction spans payment + calendar + notification).
- **Image load:** < 100 ms (via CDN).

---

## 2. High-Level Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │                  USERS                        │
                        │      Guests (search, book) & Hosts (list,     │
                        │      manage calendars, respond)               │
                        └──────────────────────┬───────────────────────┘
                                               │  HTTPS
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │            EDGE / CDN                         │
                        │   - static assets (JS/CSS)                    │
                        │   - listing photos (the bulk of CDN traffic)  │
                        │   - TLS termination, DDoS protection          │
                        └──────────┬───────────────────┬───────────────┘
                                   │                   │
                     API traffic   │                   │ image traffic
                                   ▼                   ▼
                        ┌──────────────────┐  ┌────────────────────────┐
                        │  LOAD BALANCER   │  │  IMAGE CDN             │
                        │  (L7, envoy)     │  │  (Cloudflare / Akamai) │
                        └────────┬─────────┘  └────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────────────────────┐
        ▼                        ▼                        ▼                 ▼
┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐  ┌──────────────┐
│  API Gateway   │    │  Search Service  │   │  Booking /       │  │  Media       │
│  (auth, rate   │    │  (geo + date     │   │  Reservation     │  │  Service     │
│   limit)       │    │   availability)  │   │  Service         │  │  (uploads,   │
└───────┬────────┘    └─────────┬────────┘   └────────┬─────────┘  │   processing)│
        │                       │                     │            └──────┬───────┘
        │                       │                     │                   │
        ▼                       ▼                     ▼                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              MICROSERVICES                                   │
│  ┌──────────┐ ┌────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐│
│  │ Listing  │ │ Payment    │ │ Review      │ │ User /      │ │ Pricing /   ││
│  │ Service  │ │ Service    │ │ Service     │ │ Trust       │ │ Pricing     ││
│  │          │ │ (Stripe,   │ │             │ │ Service     │ │ Engine      ││
│  │          │ │  PayPal)   │ │             │ │             │ │             ││
│ │           │ │            │ │             │ │             │ │             ││
│  └────┬─────┘ └─────┬──────┘ └─────┬───────┘ └──────┬──────┘ └─────┬───────┘│
└───────┼─────────────┼──────────────┼────────────────┼─────────────┼────────┘
        │             │              │                │             │
        ▼             ▼              ▼                ▼             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                       │
│  ┌────────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Postgres   │ │ Payment DB   │ │ Redis    │ │ Kafka    │ │ Object Store │ │
│  │ (listings, │ │ (ACID,       │ │ (cache,  │ │ (events: │ │ (S3 / GCS:   │ │
│  │  users,    │ │  money,      │ │  search  │ │  booking,│ │  photos)     │ │
│  │  bookings) │ │  double-     │ │  results)│ │  payout) │ │              │ │
│  │            │ │  entry)      │ │          │ │          │ │              │ │
│  └────────────┘ └──────────────┘ │          │ └──────────┘ └──────────────┘ │
│                                   │          │                                │
│                 ┌─────────────────┘          │                                │
│                 ▼                            ▼                                │
│           ┌──────────┐               ┌──────────────┐                        │
│           │Elasticsearch│             │ Analytics    │                        │
│           │ (search idx)│             │ / ML (S3 +   │                        │
│           │             │             │  Spark)      │                        │
│           └──────────┘               └──────────────┘                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Walking the diagram

1. **Edge/CDN** serves static assets and — critically — listing photos. Airbnb's CDN traffic is dominated by image bytes.
2. **API gateway** authenticates, rate-limits, and routes requests.
3. **Search service** is the front door for guests: it answers "what's available in Paris May 3–7 for 2 guests?" by combining a geo+date search index with ranking.
4. **Booking service** handles the transactional reservation flow (check availability → reserve → charge → confirm). This is the most consistency-critical path.
5. **Listing, Payment, Review, User/Trust, Pricing** services each own their data.
6. **Elasticsearch** powers search; **Postgres** holds the source-of-truth listings/bookings; **Redis** caches search results and hot listings; **Kafka** carries booking events; **object storage** holds photos.

---

## 3. Detailed Component Breakdown

### 3.1 Search Service

The most complex read path in the system. When a guest searches "Paris, May 3–7, 2 guests":

1. **Geographic query:** find all listings whose location is within the search area (bounding box or radius around a point).
2. **Date availability filter:** filter out listings that are already booked for any night in the requested range.
3. **Capacity filter:** only listings that can sleep ≥ 2 guests.
4. **Ranking:** order by a model that predicts likelihood of booking (price, quality, photo quality, host responsiveness, search relevance, guest host preferences).
5. **Pricing:** compute the total price for the stay (nightly rate × nights + cleaning fee + service fee + taxes).
6. **Return:** paginated results with listing cards (photo, title, price, rating).

**The hard part: date availability.** A listing is available for May 3–7 only if it has no overlapping booking AND the host's calendar is open for all those nights. This is a "gaps in a timeline" problem.

Airbnb historically used Elasticsearch for the geo+attribute search and a separate availability store for date filtering. Modern Airbnb uses a more integrated approach but the conceptual split remains.

### 3.2 Listing Service

- Owns listing metadata: title, description, amenities, location (lat/lng), capacity, photos, house rules.
- Hosts create and edit listings here.
- CRUD with rich media (photos).
- The canonical listing record lives in Postgres; search index in Elasticsearch is derived from it.

### 3.3 Booking / Reservation Service

The transactional core. Responsibilities:

- **Check availability** for the requested dates (no overlap with existing bookings or blocked calendar).
- **Reserve** the dates (mark them as unavailable atomically).
- **Charge** the guest via the Payment Service.
- **Create a reservation record.**
- **Notify** host and guest.
- **Handle failures:** if payment fails, release the reservation.

**Critical requirement:** no double-booking. Two guests trying to book the same listing for overlapping dates must not both succeed. This requires a **lock or a strong consistency constraint** on the calendar (see §9 trade-offs).

### Booking state machine:

```
                  ┌────────────┐
                  │   PENDING  │  (guest clicked "Reserve", payment authorized)
                  └─────┬──────┘
                        │ payment captures
                        ▼
                  ┌────────────┐         ┌────────────┐
                  │ CONFIRMED  │────────▶│  COMPLETED │ (stay finished)
                  └─────┬──────┘         └────────────┘
                        │
                        │ guest cancels / host cancels / payment fails
                        ▼
                  ┌────────────┐
                  │  CANCELLED │
                  └────────────┘
```

### 3.4 Payment Service

- Integrates with payment gateways (Stripe, PayPal, Adyen, regional methods).
- **Guest pays upfront** (at booking time) — Airbnb holds the funds.
- **Host paid after check-in** (or 24h after check-in) — this protects guests from no-show hosts.
- Handles **escrow**: money sits with Airbnb between booking and payout.
- **Multi-currency:** prices displayed in the guest's currency; underlying settlement in host's currency with FX conversion.
- **Payouts:** to host bank accounts via ACH/SEPA/wire.
- **Risk / fraud detection:** blocks suspicious transactions.

### 3.5 Review Service

- Guests review stays after check-out; hosts review guests.
- Double-blind: neither sees the other's review until both submit (or 14 days pass).
- Reviews are public and central to trust.
- Append-only; indexed for listing pages and search ranking.

### 3.6 User / Trust Service

- User profiles, identity verification (government ID, selfie match).
- Host guarantees (insurance against damage).
- Account security (2FA, session management).

### 3.7 Pricing Engine

- Computes the price for a given listing + dates.
- Inputs: base nightly rate, cleaning fee, service fee, occupancy tax, **dynamic pricing** (Smart Pricing — host opt-in algorithmic pricing based on demand).
- Must be consistent between search results and the booking page.

### 3.8 Media Service

- Handles photo uploads from hosts.
- **Processing pipeline:** resize to multiple resolutions, generate thumbnails, EXIF stripping, ML-based photo categorization (cover photo selection, living room vs. bedroom).
- Stores in object storage (S3/GCS); serves via CDN.

### 3.9 Event Pipeline (Kafka) & Analytics

- Every booking, search, view, and message is an event.
- Powers analytics dashboards, ML ranking model training, and host-facing insights ("views, bookings, revenue").
- Stream processing for real-time metrics (e.g., trending destinations).

---

## 4. Data Model

### 4.1 Listings (Postgres)

```
listings
────────────────────────────────────────
listing_id      UUID PK
host_id         UUID FK
title           TEXT
description     TEXT
lat             NUMERIC(9,6)      -- latitude
lng             NUMERIC(9,6)      -- longitude
street_address  TEXT               -- kept separate from public-facing
city            TEXT
country_code    CHAR(2)
property_type   TEXT               -- 'apartment','house','hotel room'
room_type       TEXT               -- 'entire place','private room','shared room'
accommodates    INT                -- max guests
bedrooms        INT
bathrooms       NUMERIC
amenities       TEXT[]              -- array: ['wifi','kitchen','pool',...]
price_cents     INT                -- base nightly price in cents
cleaning_fee    INT
guests_included INT
extra_person_fee INT
listing_currency CHAR(3)           -- ISO 4217
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
is_active       BOOLEAN
```

**Geo indexing:** Postgres supports geo indexes via the **PostGIS** extension. A `GEOGRAPHY(POINT)` column with a GIST index enables efficient "find all listings within X km of this point" queries. Alternatively, a geohash-based approach can be used.

### 4.2 Users (Postgres)

```
users
────────────────────────────────────────
user_id         UUID PK
email           TEXT UNIQUE
phone           TEXT UNIQUE
password_hash   TEXT
first_name      TEXT
last_name       TEXT
birth_date      DATE
country_code    CHAR(2)
identity_verified BOOLEAN
preferred_language CHAR(2)
preferred_currency CHAR(3)
created_at      TIMESTAMPTZ
```

### 4.3 Bookings / Reservations (Postgres — ACID critical)

```
reservations
────────────────────────────────────────
reservation_id  UUID PK
listing_id      UUID FK
guest_id        UUID FK
check_in_date   DATE
check_out_date  DATE
num_guests      INT
status          TEXT               -- 'pending','confirmed','completed','cancelled'
currency        CHAR(3)
total_price_cents INT              -- snapshot of price at booking time
host_payout_cents INT
service_fee_cents INT
cleaning_fee_cents INT
tax_cents       INT
payment_id      UUID FK            -- link to payment record
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ
```

**The critical constraint: no double-booking.** Two ways to enforce this:

**Option A — exclusion constraint (Postgres native):**

```sql
CREATE TABLE reservations (
    ...,
    EXCLUDE USING gist (listing_id WITH =,
                        daterange(check_in_date, check_out_date) WITH &&)
);
```

This uses Postgres's range types and GIST indexing to **prevent any two rows from having overlapping date ranges for the same listing**. The database itself rejects double-bookings — no application-level lock needed. This is elegant and correct.

**Option B — calendar table with row-level locks:**

```sql
CREATE TABLE listing_calendar (
    listing_id   UUID,
    date         DATE,
    is_available BOOLEAN DEFAULT true,
    reservation_id UUID NULL,
    PRIMARY KEY (listing_id, date)
);

-- To book May 3-7:
SELECT * FROM listing_calendar
WHERE listing_id = $1 AND date BETWEEN '2026-05-03' AND '2026-05-06'
FOR UPDATE;  -- row-level lock, prevents concurrent booking
-- If all rows show is_available, update them and create reservation.
```

This locks the specific nights, allowing two guests to book the *same listing* for *non-overlapping* dates concurrently.

### 4.4 Reviews (Postgres)

```
reviews
────────────────────────────────────────
review_id       UUID PK
reservation_id  UUID FK            -- must have a completed stay to review
listing_id      UUID FK
reviewer_id     UUID FK
rating          INT                -- 1..5 (overall)
rating_communication INT
rating_cleanliness INT
rating_accuracy INT
rating_checkin INT
rating_value    INT
comments        TEXT
role            TEXT               -- 'guest_reviewed_host' or 'host_reviewed_guest'
created_at      TIMESTAMPTZ
```

### 5.5 Availability / Calendar (Postgres)

```
listing_calendar
────────────────────────────────────────
listing_id      UUID
date            DATE
is_available    BOOLEAN            -- host can block dates manually
default_price   INT                -- per-night price for this date (dynamic pricing)
reservation_id  UUID NULL          -- populated when booked
PRIMARY KEY (listing_id, date)
```

One row per listing per night. For 7M listings × 365 days = ~2.5B rows. Manageable in sharded Postgres, but many teams move this to a KV store (DynamoDB-style) for horizontal scale.

### 4.6 Search index (Elasticsearch)

A denormalized document per listing, optimized for search:

```json
{
  "listing_id": "abc123",
  "title": "Cozy flat near Eiffel Tower",
  "city": "Paris",
  "country": "FR",
  "location": { "lat": 48.8584, "lon": 2.2945 },
  "room_type": "entire_place",
  "accommodates": 2,
  "price_cents": 12000,
  "amenities": ["wifi", "kitchen", "washer"],
  "avg_rating": 4.8,
  "num_reviews": 152,
  "photo_url": "https://cdn.../abc123_1.jpg",
  "available_date_ranges": [
    { "start": "2026-05-01", "end": "2026-05-10" },
    { "start": "2022026-05-15", "end": "2026-05-30" }
  ]
}
```

Elasticsearch's geo queries (`geo_bounding_box`, `geo_distance`) find listings by location. The `available_date_ranges` field (a nested type) allows filtering for date availability. (In practice Airbnb uses a more sophisticated availability approach, but this is the conceptual model.)

### 4.7 Cache (Redis)

- **Search result caching:** cache the result of "Paris, May 3–7, 2 guests" for a short TTL (minutes). Identical searches from different users hit the cache.
- **Hot listing caching:** details page for a popular listing served from Redis.
- **Session state.**

### 4.8 Media (object storage)

Photos are stored as blobs in S3/GCS. The listing record stores an array of photo URLs. Multiple resolutions are pre-generated by the media pipeline.

### 4.9 Why these databases?

| Need | Choice | Postgres Range Exclusion | Reason |
|---|---|---|---|
| Listings | Postgres + PostGIS | — | Relational, geo-indexed, ACID. |
| Bookings | Postgres with EXCLUDE constraint | ✔ | Prevent double-booking at the DB level. |
| Users / payments | Postgres | — | Money requires ACID. |
| Calendar | Postgres or DynamoDB-style KV | — | One row per night; massive but simple. |
| Search | Elasticsearch | — | Geo + full-text + nested date queries. |
| Reviews | Postgres | — | Relational, append-only, indexed. |
| Images | Object store + CDN | — | Cheap blobs, edge-served. |
| Events | Kafka | — | Durable log for analytics/ML. |

---

## 4. Request Flow — Booking a Stay

This is the core guest journey. Let's trace it.

### Step 0: Search & browse

Guest enters "Paris, May 3–7, 2 guests".

```
 Guest searches
      │
      ▼
┌──────────────────┐
│  Search Service  │  1. Geo query: listings within Paris bounding box
│                  │  2. Date availability filter: exclude booked/blocked
│                  │  3. Capacity filter: accommodates >= 2
│  (Elasticsearch) │  4. Rank by booking-likelihood model
│                  │  5. Price computation per result
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Guest browses   │  6. Paginated results rendered as cards
│  results         │  7. Clicks a listing → details page (more photos, reviews)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Guest clicks    │  8. Selects dates, guest count, clicks "Reserve"
│  "Reserve"       │
└────────┬─────────┘
```

### Step 1: The booking transaction

```
 Guest clicks "Reserve"
              │
              ▼
   ┌──────────────────────┐
   │  API Gateway         │  1. Auth, rate limit
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Booking Service     │  2. BEGIN TRANSACTION
   │                      │  3. Check availability (lock calendar rows)
   │                      │     SELECT ... FOR UPDATE on listing_calendar
   │                      │     for the requested dates
   │                      │  4. If any date unavailable → ABORT, return error
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Payment Service     │  5. Charge guest via Stripe/PayPal
   │                      │  6. If payment fails → ABORT, release locks
   │                      │  7. If payment succeeds → capture payment_id
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Booking Service     │  8. Mark calendar dates as booked
   │                      │  9. INSERT reservation record
   │                      │ 10. COMMIT TRANSACTION
   │                      │ 11. Publish "booking_created" event to Kafka
   │                      │ 12. Return confirmation to guest
   └──────────┬───────────┘
              │
              ▼  (async)
   ┌──────────────────────┐
   │  Notification Svc    │ 13. Email/push to guest: "Booking confirmed"
   │                      │ 14. Email/push to host: "You have a booking!"
   └──────────┬───────────┘
              │
              ▼  (async)
   ┌──────────────────────┐
   │  Search Indexer      │ 15. Update Elasticsearch: mark dates unavailable
   │                      │     so the listing stops appearing for those dates
   └──────────────────────┘
```

### Step-by-step narrative

1. **Auth & rate limit.** Standard gateway behavior.
2–4. **Availability check with locking.** The Booking Service opens a DB transaction and locks the calendar rows for the requested dates (`SELECT ... FOR UPDATE`). If another guest is concurrently trying to book the same dates, they block until the first transaction commits or aborts. If any date is already booked or blocked by the host, the transaction aborts and the guest gets an error ("Those dates just became unavailable").
5–7. **Payment.** The Payment Service charges the guest via the gateway. If the charge fails (card declined, fraud flag), the transaction aborts, locks are released, and no reservation is created. This is critical: **payment failure must not leave a phantom reservation.**
8–10. **Commit.** Calendar dates are marked booked, reservation record inserted, transaction committed atomically. At this point the booking is durable.
11. **Event.** A `booking_created` event is published to Kafka. This decouples the transaction from downstream effects.
12. **Confirmation.** The guest gets an HTTP 200 with reservation details.
13–14. **Notifications.** Email/push to both parties.
15. **Search index update.** The listing's availability is updated in Elasticsearch so it no longer appears in search results for those dates.

**Key insight:** the transaction spans availability check + payment + calendar update + reservation creation, all within a single DB transaction (or a distributed transaction with the payment gateway). This guarantees **no double-booking** and **no phantom reservations on payment failure**.

---

## 6. Scaling Strategy

### Stateless services scale horizontally

API gateway, Search, Listing, Booking, Payment, Review services are stateless behind load balancers. Autoscale on request rate.

### Database scaling

- **Read replicas:** Listings and reviews are read-heavy. Postgres read replicas absorb read traffic; writes go to the primary.
- **Sharding:** At Airbnb's scale, even Postgres primary can become a bottleneck. Shard by `listing_id` or by geography (all Paris listings on one shard). Airbnb has written about their sharding journey.
### Search scaling

- Elasticsearch cluster sharded by geo region or by listing_id hash.
- Replicas for read throughput.
- Hot query caching in Redis.

### Image/CDN scaling

- All photos served from CDN. Origin (S3/GCS) only hit on cache miss.
- Multiple resolutions pre-generated to serve appropriate size per device.

### Caching strategy

| Layer | What | TTL |
|---|---|---|
| CDN | photos, static assets | long |
| Redis | search results, hot listing details | minutes |
| App cache | pricing computations, currency rates | minutes |
| Client cache | recently viewed listings | session |

### Multi-region

- Metadata services run in multiple regions with async replication.
- Search and booking are regionally co-located with users (a guest in Europe searches and books against EU-region infrastructure).
- Payments routed to region-appropriate gateways.

### Handling spikes

- New Year's Eve, summer weekends, festival periods create booking spikes.
- Kafka absorbs event surges.
- Stateless services autoscale.
- Search cache absorbs most read traffic.

---

## 7. Tech Stack

Airbnb has been very open about their stack (check the Airbnb Engineering Blog — "Medium/Airbnb Engineering").

| Layer | Technology |
|---|---|
| Frontend (web) | React, TypeScript (historically Backbone, then React) |
| Mobile | iOS (Swift/Objective-C), Android (Kotlin/Java) |
| Server rendering | Next.js-style SSR for SEO |
| Backend services | Ruby on Rails (historically, the monolith), now microservices in **Java, Kotlin, Go, Python** |
| API | REST + GraphQL (Airbnb open-sourced parts of their GraphQL tooling) |
| Primary database | **PostgreSQL** (heavily used, with PostGIS for geo) |
| Search | **Elasticsearch** (with custom ranking plugins) |
| Cache | **Redis** |
| Object storage | AWS S3 + CloudFront CDN |
| Event bus | **Apache Kafka** |
| Big data | S3 + Spark / Presto / Airflow (open-sourced by Airbnb!) |
| ML | Python (scikit-learn, PyTorch), custom ranking models |
| Deployment | Kubernetes (historically Mesos) |
| Observability | Prometheus, custom tracing |
| Payments | Stripe, PayPal, Adyen, regional methods |

Notable in-house / open-source tools:

- **Airflow** — data pipeline orchestration (open-sourced by Airbnb, now an Apache project).
- **Aerosolve** — open-sourced ML framework for pricing.
- **Lottie** — open-sourced animation library (Airbnb Engineering).
- **Synapse** — Airbnb's service mesh (later moved to Istio).
- **Smart Pricing** — their dynamic pricing model.

---

## 8. How YOU Can Build a Simplified Version

A weekend Airbnb clone teaches the core marketplace concepts.

### Scope (MVP)

- Host: create a listing (title, description, location, price, photos).
- Guest: search listings by location and dates.
- Guest: book a listing (simulate payment).
- Both: leave reviews after a stay.
- Listings show on a map.

Skip: dynamic pricing, multi-currency, identity verification, host guarantees, messaging.

### Minimal stack

```
┌────────────┐   ┌──────────────────┐   ┌──────────┐   ┌─────────┐
│  React /   │──▶│  Node.js / Rails │──▶│  Redis   │──▶│ Postgres│
│  Next.js   │   │  API             │   │  cache   │   │ + PostGIS│
│  + Leaflet │   │                  │   │          │   │  (data) │
│  (maps)    │   │                  │   │          │   │         │
└────────────┘   └──────────────────┘   └──────────┘   └─────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │  S3 / MinIO  │   (photos)
                   └──────────────┘
```

### Data model (Postgres with PostGIS)

```sql
CREATE EXTENSION postgis;

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT CHECK (role IN ('guest','host','both')),
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE listings (
    id          BIGSERIAL PRIMARY KEY,
    host_id     BIGINT REFERENCES users(id),
    title       TEXT NOT NULL,
    description TEXT,
    location    GEOGRAPHY(POINT),     -- PostGIS
    price_cents INT NOT NULL,
    accommodates INT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX listings_location_idx ON listings USING GIST(location);

CREATE TABLE listings_photos (
    listing_id  BIGINT REFERENCES listings(id),
    photo_url   TEXT,
    position    INT
);

CREATE TABLE reservations (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      BIGINT REFERENCES listings(id),
    guest_id        BIGINT REFERENCES users(id),
    check_in_date   DATE NOT NULL,
    check_out_date  DATE NOT NULL,
    status          TEXT DEFAULT 'confirmed',
    total_price_cents INT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    -- Prevent double-booking at the DB level:
    EXCLUDE USING gist (
        listing_id WITH =,
        daterange(check_in_date, check_out_date) WITH &&
    )
);
```

That `EXCLUDE` constraint is the magic. It uses Postgres range types to **prevent any two reservations from overlapping on the same listing**. The database itself rejects double-bookings — no application-level lock needed.

### Search by location (with PostGIS)

```sql
-- Find listings within 5km of a point, available for the dates
SELECT l.* FROM listings l
WHERE ST_DWithin(l.location, ST_MakePoint(:lng, :lat)::geography, 5000)
  AND NOT EXISTS (
    SELECT 1 FROM reservations r
    WHERE r.listing_id = l.id
      AND r.status = 'confirmed'
      AND daterange(r.check_in_date, r.check_out_date)
          && daterange(:check_in, :check_out)
  )
ORDER BY l.price_cents ASC
LIMIT 50;
```

`ST_DWithin` uses the GIST index for fast geo filtering. The `NOT EXISTS` subquery excludes listings with overlapping reservations.

### Booking flow (Node.js pseudo-code)

```js
app.post('/reservations', async (req, res) => {
  const { listingId, checkIn, checkOut, guestId } = req.body;
  try {
    await db.query('BEGIN');
    // The EXCLUDE constraint will abort this if dates overlap
    const nights = countNights(checkIn, checkOut);
    const price = await getPrice(listingId, checkIn, checkOut);
    await db.query(
      `INSERT INTO reservations (listing_id, guest_id, check_in_date, check_out_date, total_price_cents)
       VALUES ($1, $2, $3, $4, $5)`,
      [listingId, guestId, checkIn, checkOut, price]
    );
    await db.query('COMMIT');
    res.json({ status: 'confirmed' });
  } catch (e) {
    await db.query('ROLLBACK');
    if (e.code === '23P01') {  // exclusion_violation
      res.status(409).json({ error: 'Dates not available' });
    } else {
      res.status(500).json({ error: 'Booking failed' });
    }
  }
});
```

The `EXCLUDE` constraint throws error code `23P01` on overlap — you catch it and return a 409 Conflict. Clean.

### Frontend with maps

Use **Leaflet** (free, OpenStreetMap tiles) or **Mapbox** for the map UI. Plot listings as markers. On click, show a card with photo, price, and "Book" button.

### Photo handling

- Upload to S3/MinIO.
- Generate thumbnails with `sharp` (Node.js) or Pillow (Python).
- Serve via CloudFront/Cloudflare.

### Stretch goals

1. **Elasticsearch** for full-text search (title, description, amenities).
2. **Reviews** with the double-blind reveal logic.
3. **Messaging** between guest and host (WebSocket).
4. **Payments** with Stripe (use their escrow-like "Connect" product for split payouts).
5. **Smart Pricing** — a simple model: base price adjusted by demand (search volume for that city/dates).

### Deployment

- **Frontend:** Vercel / Netlify.
- **API:** Railway / Render / a VPS.
- **DB:** a Postgres instance with PostGIS extension (Supabase, Neon, or RDS).
- **Photos:** Cloudflare R2 or AWS S3 + CloudFront.
- **Total cost for a demo:** $0–$5.

---

## 9. Key Design Decisions & Trade-offs

### Decision 1: Database-level vs application-level double-booking prevention

**DB-level (EXCLUDE constraint):** the database itself rejects overlapping reservations. Elegant, correct, no race conditions. But it requires Postgres (or similar) and can't be distributed across shards easily.

**Application-level (distributed lock via Redis):** more flexible, works across shards, but error-prone (lock leaks, race conditions on lock release).

**Airbnb's choice:** DB-level constraints for correctness, with sharding strategies that preserve locality (all reservations for a listing on the same shard).

### Decision 2: Search via Elasticsearch vs Postgres

- **Postgres + PostGIS** can do geo search and full-text search reasonably well for small catalogs.
- **Elasticsearch** shines at scale: distributed, faster full-text, better ranking, nested queries.
- **Trade-off:** operating an ES cluster is complex; but at Airbnb's scale it's necessary.

### Decision 3: Monolith first vs microservices

Airbnb started as a Ruby on Rails **monolith** and extracted microservices over years as scale demanded. The lesson: don't start with microservices. Start monolithic, extract services when a specific service needs independent scaling or deployment.

### Decision 4: Guest pays upfront vs host paid after check-in

This is a **business logic decision** with huge technical implications:

- **Guest pays at booking:** Airbnb holds funds in escrow.
- **Host paid after check-in:** protects guests from no-show hosts.
- **Implication:** the Payment Service must hold funds for days/weeks and schedule payouts. This is a deferred-payment workflow, more complex than immediate charge-and-capture.

### Decision 5: Dynamic pricing (Smart Pricing) vs host-set prices

- Host-set prices: simple, but hosts may misprice.
- Dynamic pricing: algorithmic, optimized for occupancy/revenue, but requires ML and may annoy hosts who want control.
- **Airbnb's choice:** opt-in Smart Pricing. Hosts can use it or not. Best of both worlds.

### Decision 6: Reviews are double-blind and post-stay only

- **Double-blind:** neither party sees the other's review until both submit. Prevents retaliation and encourages honesty.
- **Post-stay only:** you can only review a stay you actually completed. Prevents fake reviews.
- **Trade-off:** fewer reviews (friction), but much higher trust.

### Decision 7: Photos dominate the experience — invest in media pipeline

- Listings with better photos get more bookings.
- Airbnb invested heavily in their media pipeline: ML-based cover photo selection, automatic categorization (living room vs bedroom), professional photography program (historically).
- **Trade-off:** significant infrastructure cost for image processing and CDN, but directly drives revenue.

---

## 10. Common Interview Questions

**Q1: How would you design Airbnb?**
A: Two-sided marketplace. Split into guest flow (search → browse → book) and host flow (list → manage calendar → respond). Core services: Search (Elasticsearch + geo), Listing (Postgres + PostGIS), Booking (Postgres with EXCLUDE constraint to prevent double-booking), Payment (Stripe + escrow), Review. Use Kafka for booking events, Redis for caching, S3 + CDN for photos.

**Q2: How do you prevent double-booking?**
A: At the database level using a Postgres EXCLUDE constraint with range types: `EXCLUDE USING gist (listing_id WITH =, daterange(check_in, check_out) WITH &&)`. This makes the database reject any insert whose date range overlaps an existing reservation for the same listing. No application-level lock needed. For sharded setups, ensure all reservations for a listing live on the same shard.

**Q3: How do you search for listings by location and availability?**
A: Two-phase. (1) Geo filter via Elasticsearch `geo_bounding_box` or PostGIS `ST_DWithin` with a GIST index. (2) Date availability filter: either a nested field in ES or a `NOT EXISTS` subquery against the reservations table. Rank results by a booking-likelihood model (price, quality, photo, host responsiveness).

**Q4: Walk me through the booking flow.**
A: (See §5.) Guest clicks Reserve → API gateway → Booking Service opens transaction → locks calendar rows (`SELECT FOR UPDATE`) → checks availability → Payment Service charges guest → if success, mark calendar booked, insert reservation, commit → publish Kafka event → return confirmation. Notifications and search index update happen async.

**Q5: Why does the guest pay upfront but the host gets paid after check-in?**
A: Trust and protection. The guest commits funds at booking (committed intent). Airbnb holds them in escrow. The host is paid only after check-in (or 24h after), ensuring the guest actually arrived and the place was as described. This protects guests from no-show hosts and gives hosts confidence they'll be paid. Technically, this requires a deferred-payout payment system.

**Q6: How do you handle pricing across multiple currencies?**
A: Store prices in a canonical currency (the listing's currency). Display in the guest's preferred currency using current FX rates (cached, refreshed periodically). At booking, snapshot the exchange rate and total in both currencies for accounting. Settlement with the host happens in their currency via regional payout methods (ACH/SEPA/wire).

**Q7: How would you scale search to 7M listings?**
A: Elasticsearch cluster sharded by geo region or listing_id hash. Replicas for read throughput. Cache hot queries in Redis. Pre-compute ranking features (popularity, quality score) and store them as denormalized fields in the ES document. Use the geo index for the first cut, then filter by attributes and dates.

**Q8: How do you handle the two-sided marketplace cold-start problem?**
A: Business strategy more than technical: subsidize one side (often supply — recruit hosts first with guarantees, professional photos, onboarding support). Geo-by-geo expansion (start in one city, achieve liquidity, expand). Technically: ensure search always returns enough results (relax filters if too few matches, show "similar nearby" results).

**Q9: How would you implement reviews?**
A: Reviews table linked to a completed reservation (you can only review a stay you had). Double-blind: both reviews are hidden until both are submitted or 14 days pass. Append-only. Reviews feed into listing ranking and host/guest trust scores. Public on the listing page.

**Q10: What if the payment succeeds but the reservation insert fails?**
A: This is why the whole flow is in a DB transaction. If using a single Postgres transaction, the payment is captured externally (Stripe) before commit — so a commit failure means we've charged the guest but have no reservation. Solution: use an idempotent payment capture with a compensating refund job, or use a two-phase commit / saga pattern where the payment is authorized (not captured) first, the reservation is committed, then the payment is captured. The saga pattern is more robust for distributed transactions.

---

## 11. Further Reading

- Airbnb Engineering Blog (medium.com/airbnb-engineering) — many posts on search ranking, sharding, Smart Pricing, payments.
- "Airbnb Engineering's 5 Design Principles" talk.
- Airflow — github.com/apache/airflow (born at Airbnb).
- Lottie — github.com/airbnb/lottie.
- PostGIS documentation — postgis.net.
- *Designing Data-Intensive Applications* (Kleppmann) — for replication, partitioning, transaction concepts.
- Stripe Connect documentation — for marketplace split payments.

---

*Last updated: July 2026. Numbers are approximate and based on public reporting / industry estimates — treat them as orders of magnitude, not exact figures.*

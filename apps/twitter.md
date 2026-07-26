# Twitter / X — System Design

> **Reader's note:** This is a deep, standalone walkthrough. Read top to bottom and you will understand how Twitter/X works end-to-end — the numbers, the boxes, the data, the trade-offs, and how to build a simplified clone yourself. No buzzwords without explanation.

---

## 1. Overview & Scale Numbers

Twitter (now **X**) is a real-time, public, short-form messaging platform. The core product is deceptively simple: a user posts a short message ("tweet"), and that message must appear in the feeds of every account that follows them — within seconds, anywhere on Earth.

### Why is this hard?

The difficulty is **fan-out**. A normal app writes once and reads once. Twitter writes once and must *push* that write to potentially millions of feeds. When a user with 100M followers tweets, the system must perform work proportional to the audience size, not the tweet size. This single fact drives almost every architectural choice below.

### Real-world scale (publicly reported / industry estimates)

| Metric | Approximate value |
|---|---|
| Monthly active users (MAU) | ~600M |
| Daily active users (DAU) | ~250M |
| Tweets per day | ~500M |
| Peak tweets/sec | ~300,000+ (during major events) |
| Avg tweets/sec (steady) | ~6,000 |
| Search queries/day | ~2B |
| Timeline renders/day | ~100B+ |
| Avg following per user | ~200–300 |
| Top accounts (followers) | 100M+ |
| Media (images/video) uploaded/day | billions of objects |

**Storage math (back-of-envelope):**

- 500M tweets/day × 500 bytes (text + metadata) ≈ **250 GB/day** of text.
- Add images/video (say avg 1MB per media tweet, 30% of tweets have media) → ~150 TB/day of media.
- Over a year, that's petabytes. Media dominates storage by ~1000:1.

**Latency targets:**

- Timeline render: **< 200 ms** at p99.
- Tweet-to-feed-visibility: **< 5 seconds** for most users.
- Tweet ingestion acknowledgement to the author: **< 200 ms**.

These numbers are the North Star. Every design decision is justified against them.

---

## 2. High-Level Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │                  USERS                        │
                        │   (mobile app, web, third-party clients)      │
                        └──────────────────────┬───────────────────────┘
                                               │  HTTPS
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │            EDGE / CDN (Akamai)               │
                        │  - static assets (JS/CSS/images)             │
                        │  - cached media (profile pics, tweet images) │
                        │  - TLS termination, DDoS scrubbing           │
                        └──────────────────────┬───────────────────────┘
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │            LOAD BALANCER (L7)                │
                        │     (envoy / proprietary LB mesh)            │
                        └──────────────────────┬───────────────────────┘
                                               │
                  ┌────────────────────────────┼────────────────────────────┐
                  ▼                            ▼                            ▼
        ┌─────────────────┐         ┌─────────────────────┐     ┌────────────────────┐
        │  API GATEWAY    │         │   MEDIA GATEWAY     │     │  STREAMING SVC     │
        │  (auth, rate    │         │   (upload, transcode│     │  (WebSocket /      │
        │   limit, routing)│        │    pipeline)        │     │   Server-Sent Evts)│
        └────────┬────────┘         └──────────┬──────────┘     └─────────┬──────────┘
                 │                             │                          │
    ┌────────────┴───────────────┐             │                          │
    ▼                            ▼             ▼                          ▼
┌────────┐  ┌──────────┐  ┌────────────┐  ┌────────┐            ┌────────────────┐
│ Tweet  │  │ Timeline │  │  User /    │  │ Media  │            │  Fanout        │
│ Service│  │ Service  │  │  Social    │  │ Store  │            │  Service       │
│ (CRUD) │  │ (read)   │  │  Service   │  │ (S3 +  │            │  (writes)      │
└───┬────┘  └────┬─────┘  └─────┬──────┘  │ CDN)   │            └───────┬────────┘
    │            │              │         └────────┘                    │
    │            │              │                                       │
    ▼            ▼              ▼                                       ▼
┌────────────────────────────────────────────┐        ┌──────────────────────────────────┐
│              PRIMARY STORAGE               │        │          CACHE LAYER             │
│  ┌─────────────┐  ┌──────────────────┐     │        │  ┌────────────┐ ┌────────────┐  │
│  │ Tweet Store │  │ User / Social DB │     │        │  │ Redis      │ │ Timeline   │  │
    │  (Sharded   │  │  (graph + shard) │     │        │  │ (counts,   │ │ Cache      │  │
    │   MySQL +   │  │                  │            │  │  sessions) │ │ (Redis /   │  │
    │   Manhattan)│  │                  │     │        │  └────────────┘ │ Manhattan)│  │
    └─────────────┘  └──────────────────┘     │        │                 └────────────┘  │
└────────────────────────────────────────────┘        └──────────────────────────────────┘

                                   │
                                   ▼
                        ┌──────────────────────────────────────────────┐
                        │          ASYNC / EVENT PIPELINE              │
                        │   Kafka / EventBus — tweet-posted,           │
                        │   follow-events, engagement events           │
                        └──────────────────────────────────────────────┘
                                   │
              ┌────────────────────┼───────────────────────────┐
              ▼                    ▼                           ▼
    ┌──────────────────┐  ┌──────────────────┐     ┌───────────────────────┐
    │  Search Indexer   │  │  Analytics /     │     │  Notification Service │
    │  (Elasticsearch / │  │  Data Warehouse  │     │  (push, email, SMS)   │
    │   Lucene cluster) │  │  (S3 + Presto)   │     │                       │
    └──────────────────┘  └──────────────────┘     └───────────────────────┘
```

### Walking the diagram

1. **Edge/CDN** serves static assets and cached media so the API servers rarely touch them.
2. **Load balancer + API gateway** authenticate, rate-limit, and route each request to the right microservice.
3. **Microservices** (Tweet, Timeline, User/Social, Media) each own their data store — no shared database across services (the "database-per-service" pattern).
4. **Fanout service** is the heart of Twitter: it takes a new tweet and pushes it into the pre-computed timeline caches of every follower.
5. **Cache layer** (Redis / Twitter's in-house Manhattan) holds pre-built timelines so reading a feed is O(1) — a single list fetch.
6. **Event pipeline** (Kafka) decouples the synchronous write path from everything slow: search indexing, analytics, notifications.

---

## 3. Detailed Component Breakdown

### 3.1 API Gateway / Edge

- **Authentication:** validates OAuth tokens / session cookies. Rejects unauthenticated traffic before it hits a service.
- **Rate limiting:** per-user and per-IP token buckets. Without this, a single script can take down the site.
- **Routing:** maps `/2/timeline` → Timeline Service, `/2/tweets` → Tweet Service, etc. Modern Twitter uses an Envoy-based service mesh.
- **Protocol:** external clients speak HTTPS/JSON (or HTTP/2). Internally, services talk via **Thrift** or **gRPC** over a custom RPC framework (Twitter historically used Finagle).

### 3.2 Tweet Service

Owns the **write path** for tweets. Responsibilities:

- Validate tweet (length, spam filter, language detection).
- Assign a unique **Snowflake ID** (see §4).
- Persist to the Tweet Store (sharded MySQL / Manhattan).
- Publish a `tweet_posted` event to Kafka.
- Return success to the author quickly (< 200 ms).

The Tweet Service itself does **not** update follower timelines — that is the Fanout Service's job, triggered asynchronously by the Kafka event. This keeps the author's write latency low.

### 3.3 Timeline Service

Owns the **read path**. When you open the app, Timeline Service:

1. Asks the Social Graph for the accounts you follow.
2. Merges two sources:
   - **Pre-computed fan-out timeline** (your home feed, built by the Fanout Service).
   - **Live pulls** for accounts you follow who are "celebrity" outliers (see §9 trade-off on hybrid fan-out).
3. Ranks/re-orders using a ML model (engagement prediction, recency, diversity).
4. Injects ads and "who to follow" suggestions.
5. Returns the final ordered list of tweet IDs.

Then, for each tweet ID, it does a **batched fetch** from the Tweet Store / cache to hydrate the full tweet objects (text, author, media refs), and returns them to the client.

**Why cache the timeline at all?** Because 250M DAU each opening the app ~50×/day = ~12.5B timeline renders/day. If every render had to scan the DB for recent tweets of 200 followees, the DB would melt. Pre-computing turns the read into a cheap list-slice.

### 3.4 User & Social Graph Service

- Owns accounts, profiles, and the **follow graph** (who follows whom).
- The follow graph is a directed graph: A follows B ≠ B follows A.
- Stored in a graph-optimized store. Twitter has used **FlockDB**, a sharded graph DB built on MySQL/Redis, optimized for adjacency-list reads ("give me everyone A follows").
- Must answer two queries extremely fast:
  - `following(user_id)` → list of followee IDs (for timeline fan-out).
  - `followers(user_id)` → list of follower IDs (for fan-out target list).

### 2.5 Fanout Service (the famous one)

This is Twitter's signature engineering problem. When tweet T is posted by author A:

1. Fanout reads `followers(A)`.
2. For each follower F, it **prepends T's ID** to F's pre-computed timeline (a Redis/Manhattan list keyed by `tl:F`).
3. It caps each timeline at ~1,500 entries to bound memory.

**The celebrity problem:** If A has 100M followers, fan-out must do 100M writes — a "celebrity" write. Twitter uses a **hybrid fan-out**:

- Normal users (< ~100k followers): full fan-out (push to every follower).
- Celebrity users (huge follower counts): **skip fan-out**. Their tweets are **pulled** at read time and merged into each reader's timeline.

This is the single most important trade-off in the whole design (detailed in §9).

### 3.6 Media Service

- Handles image/video uploads (avatar, tweet media, video via Vine-legacy / X Video).
- **Transcoding pipeline:** uploads go to S3-compatible object storage; a queue triggers workers that transcode video into multiple resolutions/bitrates (HLS adaptive bitrate streaming).
- **CDN** (Akamai historically) fronts the object store so media is served from edge nodes geographically close to users.

### 3.7 Search Service

- Inverted index built by Lucene/Elasticsearch.
- When a tweet is posted, the Search Indexer consumes the Kafka event and adds the tweet to the index within seconds.
- Supports boolean ops (`from:user`, `since:2024-01-01`, `-filter:retweets`), hashtag/mention resolution, and ranking by recency + relevance.
- At Twitter's scale this is a sharded, replicated cluster handling ~2B queries/day.

### 3.8 Notification Service

Consumes events (likes, replies, follows) and fans them out to push (APNs/FCM), email, and SMS. Heavily deduplicated and batched to avoid spamming users.

### 3.9 Event Pipeline (Kafka / EventBus)

The nervous system. Every meaningful action publishes an event:

- `tweet_posted`, `tweet_deleted`, `follow_created`, `like_created`, `impression`, `click`...

Downstream consumers (search indexer, fanout, analytics, notifications, ML training pipelines) subscribe independently. This **decouples** the fast write path from slow consumers and lets new features be added without touching the Tweet Service.

---

## 4. Data Model

### 4.1 Tweet ID generation — Snowflake

Twitter open-sourced **Snowflake**, a 64-bit ID scheme that is globally unique, sortable by time, and generated without a central coordinator:

```
┌────────────────────────────────────────────────────────────────┐
│  1 bit  │  41 bits timestamp (ms since epoch)  │  10 bits  │ 12 bits │
│ (unused)│  ~69 years of unique IDs            │  machine  │ sequence│
│         │                                     │    id     │  number │
└────────────────────────────────────────────────────────────────┘
```

- **Timestamp (41 bits):** ms-level precision → IDs are roughly time-ordered. Enables sorting tweets by ID without a separate indexed timestamp column.
- **Machine ID (10 bits):** allows up to 1,024 ID generators running concurrently.
- **Sequence (12 bits):** 4,096 IDs per ms per machine. With 1,024 machines → ~4.2B IDs/ms theoretical max.
- **Globally unique, no locks:** each generator only writes its own sequence counter; no cross-machine coordination needed.

### 4.2 Tweet Store schema (simplified)

```
tweets
─────────────────────────────────────────────────────
tweet_id        BIGINT     (Snowflake ID, PK)
author_id       BIGINT
text            VARCHAR(280)   -- or longer for premium
media_keys      LIST<BIGINT>   -- refs to media table
reply_to        BIGINT NULL    -- NULL if not a reply
quote_of        BIGINT NULL    -- NULL if not a quote
conversation_id BIGINT         -- root tweet of thread
language        CHAR(5)
created_at      TIMESTAMP      -- derived from Snowflake, but indexed too
country_code    CHAR(2) NULL
retweet_count   INT            -- denormalized counters
fav_count       INT
reply_count     INT
```

- **Sharded** by `author_id` (so all tweets by one author live together) — important for profile pages and fan-out source lookups.
- Counters (retweet/fav/reply counts) are **denormalized** — updated atomically via atomic increments, not recomputed by `COUNT(*)`.

### 4.3 User & Social Graph schema

```
users
─────────────────────────────────────────────────────
user_id         BIGINT PK
username        VARCHAR(15) UNIQUE
display_name    VARCHAR(50)
bio             VARCHAR(160)
avatar_url      VARCHAR
created_at      TIMESTAMP
verified        BOOLEAN
follower_count  INT            -- denormalized
following_count INT            -- denormalized

follows                         -- the social graph (directed)
─────────────────────────────────────────────────────
follower_id     BIGINT   (PK part 1)
followee_id     BIGINT   (PK part 2)
created_at      TIMESTAMP
```

The `follows` table is the **adjacency list** of the follow graph. Reads are hot ("who does X follow?" and "who follows X?"), so this is served from FlockDB / a graph-optimized cache, not raw MySQL, at runtime.

### 4.4 Timeline cache (Redis / Manhattan)

Not a relational table — a **sorted set / list** per user:

```
KEY: tl:<user_id>
VALUE: [tweet_id_1, tweet_id_2, ... , tweet_id_1500]   (newest first)
```

- Each entry is just a tweet ID (8 bytes). 1,500 entries = 12 KB per user.
- For 600M MAU: 600M × 12 KB ≈ **7.2 TB** of timeline cache. Large but feasible with a big Redis/Manhattan cluster.

### 4.5 Database choices — why?

| Need | Choice | Reason |
|---|--- |---|
| Tweet text + metadata | Sharded MySQL (or Manhattan, Twitter's in-house KV store) | Predictable schema, strong consistency for writes, mature sharding story. |
| Social graph | FlockDB (sharded adjacency store) | Optimized for adjacency-list reads at scale. |
| Timeline cache | Redis / Manhattan | O(1) list prepend + slice; in-memory = fast. |
| Search index | Elasticsearch / Lucene | Inverted index for full-text + boolean queries. |
| Media | S3 + CDN | Cheap blob storage; CDN for global low latency. |
| Analytics | S3 + Presto/Spark | Columnar data lake for offline batch. |
| Event bus | Kafka (Apache Kafka or in-house EventBus) | Durable, partitioned, replayable log. |

**Why not MongoDB/Cassandra for everything?** Because different workloads have different shapes. Timeline reads want an in-memory list; tweets want sharded relational; search wants an inverted index. Polyglot persistence — pick the right tool per workload.

---

## 5. Request Flow — Posting & Reading a Tweet

### 5.1 Posting a tweet (write path)

```
 User types tweet & hits "Post"
              │
              ▼
   ┌──────────────────────┐
   │  Mobile App / Web    │  1. POST /2/tweets with text + optional media
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │   Edge / CDN / LB    │  2. TLS termination, WAF, DDoS check
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │   API Gateway        │  3. Validate OAuth token, check rate limit
   └──────────┬───────────┘
              │
              ▼
   └──────────────────────┐
   │  Media upload (if any)│  4a. If media: upload to S3, trigger transcode
   │  returns media_key    │      pipeline, get back a media_key
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │   Tweet Service      │  5. Generate Snowflake ID
   │                      │  6. Validate text, spam check, language detect
   │                      │  7. Persist tweet to Tweet Store (sharded MySQL)
   │                      │  8. Publish "tweet_posted" event to Kafka
   │                      │  9. Return tweet_id to app (HTTP 200)
   └──────────┬───────────┘
              │
              ▼  (async — author already got 200)
   ┌──────────────────────┐
   │   Fanout Service     │ 10. Consume "tweet_posted" event
   │                      │ 11. Look up followers(author_id) from Social Graph
   │                      │ 12. For each follower F (non-celebrity path):
   │                      │       LPUSH tl:<F> <tweet_id>
   │                      │       LTRIM tl:<F> 0 1499   (cap at 1500)
   │                      │ 13. If author is celebrity → skip (pull at read)
   └──────────┬───────────┘
              │
              ├──────────────────────────────────────────────┐
              ▼                                              ▼
   ┌──────────────────────┐                     ┌──────────────────────┐
   │  Search Indexer      │ 14a. Add tweet to    │ Notification Service │ 14b. Notify
   │  (Lucene/ES)         │     Lucene index     │                      │     mentions/@replies
   └───────────────────────┘                     └──────────────────────┘
```

**Key insight:** the author's HTTP request returns at **step 9**. Everything after that (fanout, indexing, notifications) happens **asynchronously**. This is what keeps write latency under 200ms even for authors with many followers.

### 5.2 Reading the home timeline (read path)

```
 User opens app
              │
              ▼
   ┌──────────────────────┐
   │  Mobile App / Web    │  1. GET /2/timeline?cursor=...
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Timeline Service    │  2. Fetch pre-computed timeline from cache:
   │                      │     LRANGE tl:<user> 0 19   (first 20 tweet IDs)
   │                      │ 3. Fetch celebrity followee tweets since last open
   │                      │     (pull path — not in the cached list)
   │                      │ 4. Merge + rank (recency + engagement model)
   │                      │ 5. Inject ads + recommendations
   │                      │ 6. Batch-fetch full tweet objects by ID from
   │                      │     Tweet Store / cache (hydrate)
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Mobile App / Web    │  7. Render feed. Poll for new tweets or receive
   │                      │     via WebSocket / Server-Sent Events.
   └──────────────────────┘
```

**Cost of a timeline read:** essentially one Redis `LRANGE` + one batched tweet hydration. Both are O(timeline_size), not O(following_count). That is why Twitter can serve 100B+ timeline renders/day.

---

## 6. Scaling Strategy

### Horizontal scaling of stateless services

Tweet, Timeline, User, Fanout, Search services are all **stateless** — they keep no per-request state in memory. So you scale them by adding machines behind the load balancer. Autoscaling groups react to CPU/queue-depth.

### Sharding the data

- **Tweet Store** sharded by `author_id`. A consistent hash maps `author_id → shard`. All tweets by one author co-locate, which makes profile pages and fan-out source lookups local to one shard.
- **Social Graph (FlockDB)** sharded by `follower_id` for the "who do I follow?" query and by `followee_id` for the "who follows me?" query — dual sharding because both directions are hot.

### Caching layers

| Layer | What | TTL / Strategy |
|---|---|---|
| CDN | static assets, profile images, tweet images | long TTL, cache on first miss |
| Object cache (Redis) | tweet objects, user profiles, counts | TTL minutes; write-through |
| Timeline cache (Redis/Manhattan) | pre-built feed per user | continuous, capped at 1500 |
| App-level (client) | last-rendered feed | client-side, short TTL |

### Read replicas

Each MySQL shard has primary + N read replicas. Writes go to primary; reads fan out across replicas. Replication lag is a constant concern — Twitter uses semi-sync replication and careful routing to avoid reading stale data for the author's own tweets.

### Fan-out scaling

- **Normal users:** push to all followers (write-amplification, but bounded by follower count).
- **Celebrities:** pull at read time (read-amplification, but bounded by how often their tweets are actually read).
- The crossover threshold (~tens of thousands of followers) is tuned empirically.

### Multi-region deployment

- Active-active across regions (US-East, US-West, EU, Asia) for resilience and latency.
- Data is replicated with region-local primaries for hot data; cross-region async replication for durability.
- **Gizonos** (Twitter's internal geo-DNS) routes users to the nearest healthy region.

### Handling traffic spikes

During the Super Bowl / World Cup / election nights, tweets/sec can 10–50×. Strategies:

- Pre-provisioned burst capacity (extra idle instances).
- **Shedding load gracefully** — degrade non-critical features (e.g., disable view-counts) before failing core tweet/timeline.
- Kafka as a shock absorber: fanout/indexer can fall behind briefly; user experience degrades to "slightly delayed timeline" rather than outage.

---

## 7. Tech Stack

Twitter has been candid about its stack over the years. Representative choices:

| Layer | Technology |
|---|---|
| Frontend (web) | React (modern), historically Flight + custom MVC |
| Mobile | iOS (Swift), Android (Kotlin) |
| Edge / CDN | Akamai, internal edge nodes |
| API gateway / mesh | Envoy, Finagle (Scala) |
| Services | Scala (Finagle), Java, some Go and Python |
| RPC | Thrift (historically), gRPC |
| Primary storage | MySQL (heavily sharded), Manhattan (in-house KV) |
| Graph store | FlockDB (sharded adjacency store on MySQL/Gizzard) |
| Cache | Redis, Twemproxy/NutCracker (historically), Manhattan |
| Search | Lucene (custom distributed build, "Earlybird"), Elasticsearch |
| Object storage | S3-compatible (internal), Azure Blog |
| Media processing | FFmpeg, custom transcode farm |
| Event bus | Apache Kafka, EventBus (in-house) |
| Data warehouse | S3 + Vertica/Presto/Spark |
| Deployment | Mesos/Aurora historically; Kubernetes today |
| Observability | Zipkin (distributed tracing, born at Twitter), VOps |

Notable in-house systems:

- **Finagle** — RPC framework, origin of the famous "Finagle" stack, heavily concurrency-oriented Scala.
- **Zipkin** — distributed tracing (Twitter birthed the idea at scale).
- **Snowflake** — ID generation (open-sourced).
- **Manhattan** — real-time, distributed KV store.
- **Earlybird** — custom Lucene-based real-time search engine.
- **FlockDB** — sharded graph DB.
- **Twemproxy / NutCracker** — Redis proxy/multiplexer.

---

## 8. How YOU Can Build a Simplified Version

A weekend-scale Twitter clone teaches you 80% of the concepts. Here's a pragmatic blueprint.

### Scope (MVP features)

- Register/login.
- Post a tweet (text only, 280 chars).
- Follow / unfollow.
- Home timeline (tweets from people you follow, newest first).
- User profile page (your own tweets).

Skip: media, search, trending, notifications, DMs. Add them later.

### Minimal stack

```
┌────────────┐   ┌──────────────────┐   ┌──────────┐   ┌─────────┐
│  React /   │──▶│  Node.js / Flask │──▶│  Redis   │──▶│ Postgres│
│  Next.js   │   │  API (Express)   │   │  cache   │   │  (data) │
└────────────┘   └──────────────────┘   └──────────┘   └─────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │   BullMQ /   │   (job queue for fanout,
                   │   Redis queue│    powered by Redis)
                   └──────────────┘
```

### Data model (Postgres)

```sql
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    bio           TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE tweets (
    id          BIGSERIAL PRIMARY KEY,
    author_id   BIGINT NOT NULL REFERENCES users(id),
    text        VARCHAR(280) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON tweets (author_id, created_at DESC);

CREATE TABLE follows (
    follower_id BIGINT NOT NULL REFERENCES users(id),
    followee_id BIGINT NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (follower_id, followee_id)
);
CREATE INDEX ON follows (followee_id);   -- for "who follows me?"
```

### Timeline — two simple strategies

**Strategy A: Pull (query at read time)** — simplest, fine for small scale:

```sql
SELECT t.* FROM tweets t
JOIN follows f ON f.followee_id = t.author_id
WHERE f.follower_id = :me
ORDER BY t.created_at DESC
LIMIT 50;
```

This is a single JOIN. Works great up to ~10k users / few hundred QPS. Beyond that the JOIN becomes expensive.

**Strategy B: Push (fan-out on write)** — closer to real Twitter:

When a tweet is posted, push its ID into each follower's Redis list:

```js
// On tweet post:
await db.query("INSERT INTO tweets ...");
await queue.add("fanout", { tweetId, authorId });

// Fanout worker:
const followers = await db.query(
  "SELECT follower_id FROM follows WHERE followee_id=$1", [authorId]);
for (const f of followers.rows) {
  await redis.lpush(`tl:${f.follower_id}`, tweetId);
  await redis.ltrim(`tl:${f.follower_id}`, 0, 999);  // cap
}

// On timeline read:
const ids = await redis.lrange(`tl:${me}`, 0, 49);
const tweets = await db.query("SELECT * FROM tweets WHERE id = ANY($1)", [ids]);
```

Start with Strategy A, switch to B when the JOIN gets slow. That's the exact evolution Twitter itself went through.

### Deployment

- **Frontend:** Vercel or Netlify (free tier).
- **API:** Railway, Render, or a $5 VPS with Docker.
- **DB:** Supabase or Neon (managed Postgres free tier).
- **Cache/queue:** Upstash (serverless Redis) or a Redis container.
- **Total cost for a demo:** $0.

### Stretch goals (once MVP works)

1. Add image uploads → S3 + CloudFront.
2. Add search → spin up a Meilisearch or Typesense container, index tweets on post.
3. Add real-time updates → WebSocket (Socket.io) or Server-Sent Events.
4. Add Snowflake-style IDs → `bigint` generated by your app, not DB autoincrement (so you can shard later).
5. Sharding practice → use Citus or Postgres partitioning by `author_id`.

---

## 6. Key Design Decisions & Trade-offs

### Decision 1: Push (fan-out on write) vs Pull (fan-out on read)

| | Push (fan-out on write) | Pull (fan-out on read) |
|---|---|---|
| **Write cost** | O(followers) — expensive for celebrities | O(1) — just store the tweet |
| **Read cost** | O(1) — read pre-built list | O(followees) — must query/merge many sources |
| **Freshness** | Near-real-time (ms after write) | Always fresh (computed on read) |
| **Storage** | A timeline cache per user | None extra |
| **Failure mode** | If fanout lags, tweet missing from some feeds | Always works |
| **Best for** | Most users (small follower sets) | Celebrities (huge follower sets) |

**Twitter's choice:** Hybrid. Push for normal users, pull for celebrities. The crossover is tuned based on empirical write/read cost ratios.

### Decision 2: Snowflake IDs vs DB auto-increment

- Auto-increment: simple, but centralizes ID generation at one DB primary — a bottleneck and single point of failure.
- Snowflake: distributed, time-ordered, no coordination. Cost: slightly more complex; IDs are not contiguous.

### Decision 3: Relational (MySQL) vs KV (Manhattan) for tweet storage

- MySQL: strong consistency, transactions, rich queries. Mature sharding tooling.
- Manhattan (KV): higher write throughput, simpler scaling model, but weaker query semantics.
- Twitter uses **both** — Manhattan for hot key-value access, MySQL for records needing transactional integrity.

### Decision 4: Pre-compute timelines vs compute on read

Pre-computing trades **storage for latency**. 600M users × 12KB = ~7TB of cache. That's expensive, but it turns the hottest read path (timeline) into an O(1) Redis operation. Worth it.

### Decision 5: Single region vs multi-region active-active

Multi-region active-active doubles infrastructure cost but provides:

- **Latency:** users hit a nearby region.
- **Resilience:** an entire region can fail without downtime.

Twitter runs active-active across multiple regions with async cross-region replication. The cost is accepting brief windows of cross-region inconsistency for non-critical data.

### Decision 6: Cap timeline cache at ~1,500 entries

Why not more? Memory cost. 1,500 × 8 bytes × 600M users ≈ 7TB. Doubling to 3,000 doubles memory. Users rarely scroll past a few hundred tweets anyway, and older entries can be pulled from the DB on deep scroll.

### Decision 7: At-most-once vs at-least-once fan-out

Kafka delivers events **at-least-once**. A crashed fanout worker might reprocess a `tweet_posted` event. This is fine because:

- Adding a tweet ID to a timeline list twice → client deduplicates by tweet ID.
- Removing a tweet on delete must be eventually consistent across all timelines.

The system prefers **idempotent operations** over exactly-once semantics, which are expensive.

---

## 9. Common Interview Questions

**Q1: How would you design Twitter's timeline?**
A: Start with the read/write asymmetry. Most users have < few hundred followers and read their timeline 50×/day. Pre-computing each user's timeline on write (fan-out-on-write) turns the hot read path into an O(1) Redis LRANGE. For celebrity authors with millions of followers, fan-out-on-write is too expensive, so skip them and pull their tweets at read time (hybrid fan-out). Cap each timeline cache at ~1500 entries to bound memory.

**Q2: How do you handle the celebrity tweet problem (Justin Bieber has 100M followers)?**
A: Hybrid fan-out. Below a follower threshold (say ~30k–100k), push to all followers. Above it, don't push — instead, at read time, fetch the celebrity's recent tweets and merge them into the reader's pre-computed timeline. This bounds write cost for celebrities while keeping read latency low for everyone else.

**Q3: Walk me through what happens when you post a tweet.**
A: (See §5.1.) Auth → API gateway → Tweet Service → assign Snowflake ID → persist to sharded store → publish Kafka event → return 200 to author. Asynchronously, Fanout Service consumes the event, looks up followers, and prepends the tweet ID to each follower's Redis timeline list. Search indexer and notification service consume the same event independently.

**Q4: Why use Snowflake IDs instead of auto-increment?**
A: Auto-increment requires a single primary to assign IDs, creating a bottleneck and SPOF. Snowflake encodes timestamp + machine ID + sequence, so any of ~1000 machines can generate globally unique, roughly-time-ordered IDs without coordination. Time-ordering also lets us sort tweets by ID without a separate sort key.

**Q5: How would you scale this to 500M tweets/day?**
A: Stateless services scale horizontally behind LBs. Shard the Tweet Store by author_id. Cache hot tweets and timelines in Redis/Manhattan. Use Kafka to decouple write path from slow consumers (fanout, search, analytics). Multi-region active-active for latency and resilience. Cap and shed non-critical load during spikes.

**Q6: How do you keep timeline read latency under 200ms?**
A: Pre-compute the timeline in an in-memory list. Reading is just `LRANGE` + batched hydration. No JOINs, no scanning. Celebrties are pulled and merged in parallel. Rank/ads injection is bounded work. Edge CDNs cache static rendering assets. Redis cluster with tens of thousands of nodes provides the aggregate throughput.

**Q7: Where does the inconsistency window live, and how do you handle it?**
A: Between tweet post and fanout completion (async via Kafka). A follower might not see a tweet for a few seconds. This is acceptable. For the author themselves, Tweet Service can write-through to the author's own timeline immediately so their own feed is instant. Deletes propagate similarly via a `tweet_deleted` event; until then, a tweet may linger in some timelines — acceptable, eventually consistent.

**Q8: Why use Kafka (event bus) instead of direct service-to-service calls?**
A: Decoupling. The Tweet Service doesn't know or care about search, analytics, or notifications. Adding a new consumer (e.g., a new ML feature) doesn't require changing Tweet Service. Kafka also acts as a shock absorber during traffic spikes — consumers can lag slightly instead of the whole system failing.

**Q9: How do you store and serve media (images/video)?**
A: Upload to object storage (S3-compatible), trigger async transcode pipeline for video (multiple bitrates via HLS), serve through a CDN. The tweet stores only a reference (media_key) to the media object. CDN handles 99% of media reads; origin (S3) is only hit on cache miss.

**Q10: How would you implement search?**
A: Inverted index via Lucene/Elasticsearch. Consume `tweet_posted` events and add to index in near-real-time. Index tokens (words, hashtags, mentions). Support boolean operators and filters. Shard the index across many nodes; replicate for throughput. Rank by recency + relevance signals (engagement, authority).

---

## 10. Further Reading

- Twitter Engineering Blog (many posts on fan-out, Earlybird, Manhattan, Snowflake).
- "Timelines at Twitter" (Twitter Engineering, ~2013, still canonical).
- Snowflake — github.com/twitter-archive/snowflake
- FlockDB — github.com/twitter-archive/flockdb
- Zipkin — zipkin.io
- *Designing Data-Intensive Applications* (Kleppmann) — chapters on replication, partitioning, and batch/stream processing.

---

*Last updated: July 2026. Numbers are approximate and based on public reporting / industry estimates — treat them as orders of magnitude, not exact figures.*

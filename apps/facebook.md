# Facebook — System Design Atlas

> **Audience:** A developer transitioning to AI/ML engineering who wants to understand how Facebook is built end-to-end. Plain English, real numbers, ASCII diagrams, basics-first.

---

## 1. Overview & Scale Numbers

Facebook is the largest social network ever built — a real-time feed of posts, photos, videos, comments, likes, messages, live video, and Marketplace listings, all personalized for ~3 billion people. It is the canonical example in systems design because almost every scaling technique in the textbook was either invented or stress-tested here.

### Scale (public numbers + estimates)

| Metric | Value | Why it matters |
|---|---|---|
| Monthly active users (MAU) | ~3 billion | Largest social network on Earth |
| Daily active users (DAU) | ~2 billion | Engagement depth |
| Photos uploaded per day | ~350+ million | Storage and bandwidth dominate |
| News Feed stories generated per day | trillions of candidate stories | Ranking is the core compute cost |
| Likes per second (peak) | ~10+ million | Extreme write QPS on a hot counter |
| Messages per day (Messenger) | ~20+ billion | Separate realtime subsystem |
| Videos watched per day | ~8+ billion views | Largest video platform after YouTube |
| Data stored | hundreds of EB (exabytes) | Custom storage was a necessity |
| Data centers | 15+ hyperscale data centers globally | Geo-distribution for latency + resilience |
| Engineers | ~20,000+ (Meta total) | Massive engineering org |

### Why the numbers matter

Facebook's defining engineering challenge is **fanout + ranking at population scale**. When you open the app, the system must: find everything your friends and followed pages posted, filter out spam/dead/old content, rank ~2,000 candidate stories down to ~30 you'll actually see, and render them with media — all in **under 100 ms**. That pipeline runs ~2 billion times per day. Almost every architectural choice exists to make that pipeline fast and cheap.

### The one-paragraph summary

Facebook is a **write-once, read-many, ranked-feed system**. Writes (posts, likes, comments) hit a database and get fanned out to a precomputed index per user (the "home feed" cache). Reads (opening the app) pull from that index, rank stories with an ML model, and return them. Everything else — Messenger, Live, Marketplace — is a separate subsystem bolted onto the same identity graph.

---

## 2. High-Level Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              USER DEVICES                                    │
 │           iOS · Android · Web · mobile web (mbasic) · Lite app               │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │  HTTPS
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                          EDGE / POP NETWORK                                  │
 │  ┌──────────────┐  ┌──────────────┐  └─────────────────────────────────────┐ │
 │ │  PoP (Point   │  │  Edge Cache  │  │  TLS termination + HTTP/3            │ │
 │ │  of Presence) │  │  (Varnish)   │  │  Photo/Video CDN edge                │ │
 │ │  in every ISP │  │              │  │                                      │ │
 │ │  city         │  └──────────────┘  └─────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                            API / BFF LAYER                                   │
 │              (Graph API / GraphQL / Mobile BFF)                              │
 │   - Auth, rate limit, request batching, response composition                 │
 │   - One mobile request fans out to 10s of microservices                      │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
        ┌──────────────────────────────────────────────────────────────┐        │
        │                                                              │        │
        ▼                    ▼                  ▼                       ▼        ▼
 ┌─────────────┐    ┌──────────────┐    ┌──────────────┐      ┌──────────────────┐│
 │  News Feed  │    │  Timeline    │    │  TAO (Graph) │      │  Search /        ││
 │  Service    │    │  Service     │ │  (social graph)│      │  Elasticsearch   ││
 │  - ranking  │    │  - profile   │    │  nodes+edges  │      │                  ││
 │  - fanout   │    │    feed      │    │               │      └──────────────────┘│
 └─────────────┘    └──────────────┘    └──────────────┘                          │
        │                                                                      │
        ▼                                                                      │
 ┌─────────────────────────────────┐                                           │
 │   FEED RANKING (ML)             │                                           │
 │   - trillions of candidates/day │                                           │
 │   - models: GBDT → DNN → DL RM  │                                           │
 └─────────────────────────────────┘                                           │
        │                                                                      │
        ▼                                                                      │
 ┌──────────────────────────────────────────────────────────────────────────────┐│
 │                            STORAGE LAYER                                     ││
 │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────┐  ┌─────────┐ ││
 │ │ TAO graph   │  │ Haystack   │  │ Cassandra / │  │ MySQL    │  │ Redis   │ ││
 │ │ (nodes+edges│  │ (photo/vid │  │  ScyllaDB   │  │ (sharded │  │ (cache, │ ││
 │ │  memcached) │  │  blob)     │  │  (messages) │  │  social  │  │  counters│ ││
 │ │             │  │            │  │             │  │  data)   │  │  ETL queue│ ││
 │ └────────────┘  └────────────┘  └─────────────┘  └──────────┘  └─────────┘ ││
 └──────────────────────────────────────────────────────────────────────────────┘│
```

### Layered mental model

1. **Edge/POP layer** — Facebook places servers **inside ISP datacenters** in major cities. Static content (photos, videos) is cached at the edge so it never crosses the long-haul network.
2. **API/BFF layer** — the mobile app makes one "give me my feed" request; the BFF fans it out to dozens of microservices and stitches the response together.
3. **Core services** — News Feed, Timeline, Graph (TAO), Search. These are the brains.
4. **Storage layer** — a zoo of specialized databases, each picked for a specific workload. This is where Facebook's deepest innovations live.

---

## 3. Detailed Component Breakdown

### 3.1 News Feed Service (the heart)

- **What:** Generates and ranks the feed you see when you open Facebook.
- **Why it exists:** With 5,000 potential candidate stories per user per session, unranked feed would be garbage. The News Feed service pulls candidates, runs ML ranking, and returns ~30 stories.
- **Pipeline:**
  1. **Candidate generation** — pull from the user's precomputed feed index (fanout-out writes, see below).
  2. **Filtering** — remove spam, deleted posts, content you've already seen.
  3. **Feature extraction** — who posted, post type, recency, your affinity to the author, media type, predicted engagement.
  4. **Ranking** — ML model scores each candidate. Historically GBDT → DNN → modern multi-task DNN.
  5. **Dedup/shelf logic** — avoid showing 5 similar stories in a row.
  6. **Return** — ship the top ~30 to the client.

### 3.2 Fanout Service (the write path)

- **What:** When Alice posts, this service writes Alice's post to **every friend's feed index**.
- **Why it exists:** Two ways to build a feed:
  - **Pull (read-time):** When Bob opens the app, query "all posts by Bob's friends in last N hours," sort. Expensive at read time, cheap at write time. Good for users with few friends.
  - **Push (write-time / fanout-on-write):** When Alice posts, push a reference into each friend's precomputed feed. Cheap at read time, expensive at write time. Good for normal users.
  - **Hybrid:** Facebook uses **fanout-on-write for normal users** and **falls back to pull for celebrities** (a celeb with 50M followers would explode the fanout — see §9).
- **Implementation:** The post is written once to the post store; a pointer is pushed to each friend's feed index in a Redis-like cache.

### 3.3 TAO — The Social Graph

- **What:** Facebook's distributed graph database. Nodes = users, posts, photos, comments. Edges = friendships, likes, author-of.
- **Why it exists:** "Friends of friends who liked X" is a graph query. SQL is a poor fit. TAO caches the graph in memcached and backs it with MySQL.
- **Pattern:** Objects (nodes) + Associations (edges) with a TTL. Reads dominate (100x writes), so memcached sits in front.
```
   ┌────────────┐     ┌──────────────┐     ┌──────────────┐
   │ TAO API    │────▶│  Memcached   │────▶│  MySQL       │
   │ (graph     │     │  (graph cache│     │  (durable    │   ...
   │  queries)  │     │   hot path)  │     │   graph)     │
   └────────────┘     └──────────────┘     └──────────────┘
```

### 3.4 Haystack — Photo/Video Blob Store

- **What:** Facebook's custom photo storage system. Photos are stored as append-only logs with an in-memory index.
- **Why it exists:** Standard filesystems die when you store 350 million photos/day. Haystack eliminates filesystem metadata overhead by rolling its own.
- **How:** Photos appended to huge volume files; an in-memory index maps photo-ID → (volume, offset). Reads are O(1) with the index in RAM.

### 3.5 Memcached Layer

- **What:** Thousands of memcached instances forming the world's largest distributed cache.
- **Why it exists:** Reads dominate writes by ~100x on Facebook. Every database read goes through memcached first. Lookups like "is X a friend of Y?" hit memcached, not MySQL.
- **Scale:** Trillions of requests per day. This is THE scaling lever.

### 3.6 Scribe / Puma — Data Pipeline

- **What:** Every action (like, view, scroll) is logged. Scribe funnels logs into analytics pipelines. Puma handles stream processing.
- **Why:** Powers feed ranking training data, ads targeting, integrity (spam) detection.

### 3.7 Messenger (separate subsystem)

- **What:** Real-time chat. Originally built on the same backend, then split into a dedicated system (now largely MySQL → Cassandra-style) when it outgrew the main graph.
- **Why separate:** Chat has totally different latency requirements (sub-second) and access patterns (recent messages only).

### 3.8 Live / Video Infra

- **What:** Live video ingest (RTMP from broadcaster), transcoding to multiple bitrates, global CDN distribution.
- **Why it exists:** A single live video can have millions of simultaneous viewers. Encode-once-at-source, fanout-via-CDN is the only economical model.

---

## 4. Data Model

### 4.1 TAO — the graph

TAO stores two things: **objects** and **associations**.

```
   Object:    { id, type, data (JSON), time }
   Association: { id1 (source), type, id2 (target), time, data }
```

Examples:
- Object type `user` → Alice (id=1)
- Object type `post` → "Had a great coffee!" (id=999)
- Association `friend` → (Alice → Bob)
- Association `author_of` → (Alice → post 999)
- Association `like` → (Bob → post 999)

### 4.2 MySQL schema (TAO's durable backend, simplified)

```sql
-- Objects (nodes)
CREATE TABLE objects (
    id           BIGINT PRIMARY KEY,
    type         VARCHAR(32),
    data         JSON,
    created_at   DATETIME,
    updated_at   DATETIME
);

-- Associations (edges) -- sharded by id1
CREATE TABLE associations (
    id1          BIGINT,      -- source node
    type         VARCHAR(32), -- friend, like, author_of...
    id2          BIGINT,      -- target node
    data         JSON,
    time         BIGINT,      -- association timestamp
    PRIMARY KEY (id1, type, id2)
);
```

- **Sharded by `id1`** → all of Alice's out-edges live on one shard → "what did Alice like?" is a single-shard query.

### 4.3 Feed index (Redis-like cache, conceptual)

```
   key:   feed:{user_id}
   value: [ post_id_1, post_id_2, post_id_3, ... ]   (sorted by time/recency)
```

When Alice posts, fanout pushes `post_id` to the `feed:{friend_id}` of each friend. When Bob opens the app, News Feed reads `feed:bob_id`, fetches post objects from TAO, ranks, returns.

### 4.4 Why this database mix?

| Data | DB | Why |
|---|---|--- relational, ACID, sharded by id1 → simple graph queries |  |
| Graph cache | Memcached | 100x read/write ratio → cache everything |
| Photos | Haystack (custom) | 250+ billion photos, FS metadata is the bottleneck |
| Messages | Cassandra | Append-only, partition-local ordering |
| Counters (likes) | Redis / custom | Atomic INCR at extreme QPS |
| Analytics | Hive / Presto on S3-like | Massive scan workloads |

*(Render note: first row "Why" = relational, ACID, sharded by id1 → simple graph queries.)*

### 4.5 Like counter — the hot path problem

A single popular post can get **10,000 likes/sec**. You can't `UPDATE counters SET count = count + 1` in MySQL at that rate. Solution: **counter sharding** — split one logical counter into N sub-counters in Redis, INCR a random one, sum on read.

```
   like_count:post_999:0   →  3,412
   like_count:post_999:1   →  3,388
   ...                     →  ...
   like_count:post_999:99  →  3,401
   TOTAL = sum of all shards
```

---

## 5. Request Flow — Posting and Seeing a Status Update

> **Alice posts "Morning!" → Bob (her friend) opens Facebook 10 minutes later.**

### Write path (Alice posts)

```
 Alice's phone                 Facebook Backend
 ─────────────                 ────────────────
      │
   1. Types "Morning!"
   2. POST /api/feed
      { text: "Morning!" }
      │
      ├──── 3. API layer ────────▶ [Auth, rate limit]
      │
      │                       4. Write Service
      │                          - create post object in TAO
      │                          - post_id assigned
      │                          - post_id stored in Alice's timeline
      │
      │                       5. Fanout Service
      │                          - fetch Alice's friend list
      │                          - for each friend F:
      │                              push post_id to feed:F
      │                          - (celeb friends use pull mode)
      │
      │◀─── 6. 200 OK ────────────
      │
      │                       7. Async:
      │                          - index post for search
      │                          - generate thumbnails (if media)
      │                          - log event for ML training
```

### Read path (Bob opens the app)

```
 Bob's phone                   Facebook Backend
 ─────────────                 ────────────────
      │
   1. Opens app
   2. GET /api/feed
      │
      ├──── 3. API layer ────────▶ [Auth]
      │
      │                       4. News Feed Service
      │                          a. Read feed:bob_id   → [post_id, ...]  (~2,000 candidates)
      │                          b. Filter: seen? spam? deleted?
      │                          c. Feature extraction (author affinity, recency, type)
      │                          d. ML ranking model scores each
      │                          e. Top ~30 stories selected
      │                          f. Fetch post objects + media URLs from TAO
      │                          g. Compose response
      │
      │◀─── 5. JSON response ─────
      │     { stories: [ ... ] }
      │
   6. Render feed
   7. For each story, request photo/video from CDN edge
```

### Why this design works

- **Read-heavy workload gets cheap reads.** Fanout-on-write means Bob's feed read is just "read a list of post IDs, fetch objects, rank" — no expensive joins.
- **Celebrities break fanout, so they use pull.** A celeb post would otherwise fan out to 50M feeds simultaneously. Hybrid approach.
- **Ranking is decoupled from storage.** The ML model can change daily without touching the storage schema.

---

## 6. Scaling Strategy

### 6.1 Custom everything

Facebook hit scale limits of off-the-shelf systems early. They built:
- **TAO** for the social graph.
- **Haystack** for photos.
- **Memcached** enhancements (mcrouter, clustering).
- **Presto**, **Scuba**, **Hive** for analytics.
- **OCP hardware** (Open Compute Project) — custom servers/Racks designed in-house.
- **OAM** networking gear.

### 6.2 Geo-DNS + PoPs

Users are routed to the nearest data center via DNS. Photos/videos cached at **PoPs inside ISP networks** so they don't cross the long-haul.

### 6.3 Sharding by user

TAO is **sharded by `id1`** (the source node). All of Alice's friendships, likes, and posts live on one shard → graph queries are single-shard.

### 6.4 Multi-datacenter replication

Databases are replicated across regions. Leader-follower with regional leaders for writes; followers serve reads. Failover is automated.

### 6.5 Cache everything

The read-to-write ratio is ~100:1. Memcached absorbs the vast majority of reads. A cache miss is treated as a bug.

### 6.6 Async pipeline for non-critical work

Indexing for search, thumbnail generation, ML training logging — all async, via Scribe → queue → worker. The post API returns before these finish.

### 6.7 Rate limiting + graceful degradation

Under load, Facebook sheds non-critical features (e.g., "friend is typing" indicators) to protect core feed.

---

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|--- distributed graph cache, sub-ms reads |  |
| Durable graph | MySQL (sharded) | ACID, well-understood |
| Photo/video blob | Haystack (custom) → f4 (video) | FS metadata bottleneck at scale |
| Messages | Cassandra / custom | Append-only, horizontal scale |
| Cache | Memcached (mcrouter) | World's largest distributed cache |
| Counters | Redis / custom | Atomic INCR at extreme QPS |
| Analytics | Hive / Presto / Scuba | Massive scan workloads |
| ML ranking | PyTorch + custom serving | Multi-task DNN for feed ranking |
| Frontend | React (invented here!) | Component model, SSR |
| Backend lang | Hack (PHP dialect), C++, Python, Rust | Hack for velocity, C++ for perf |
| Mobile | React Native (some), native (some) | Cross-platform velocity |
| Load balancing | Custom L4/L7 + Proxygen (HTTP) | HPACK, HTTP/3 |
| Config | Zeus | Internal service config |
| Hardware | OCP servers, custom network | Cost, supply chain control |

*(Render note: first row "Why" = distributed graph cache, sub-ms reads.)*

### Why Hack (PHP dialect)?

Facebook's original backend was PHP — fast to write, huge talent pool. But PHP's runtime was slow at scale. They wrote **HHVM** (a PHP JIT) and **Hack** (a gradually-typed PHP) to keep developer velocity while gaining performance and type safety. This is a classic "improve the tool you have" vs "rewrite in Go/Rust" decision.

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Tech choices

| Concern | Choice | Why |
|---| simple, ubiquitous, realtime if needed |  |
| Backend | Node.js + Express OR Python + FastAPI | Fast iteration |
| DB | PostgreSQL | Relational + JSONB for graph-ish data |
| Cache | Redis | Feed index, counters |
| Object storage | S3 | Photos/videos |
| Search | Meilisearch or Postgres FTS | Simple post search |
| Frontend | React + Next.js | SSR, components |
| Hosting | Vercel (frontend) + Render/Railway (backend) | Easy deploy |

*(Render note: first row label/why got merged in some viewers. It should read: Protocol = HTTP/REST — because it's simple, ubiquitous, realtime if needed via WebSockets.)*

### 8.2 Build order

1. **Auth + User model.** Email/password, JWT sessions.
2. **Post + Timeline.** `posts` table, `POST /api/posts`, `GET /api/feed`.
3. **Friendships.** `friendships` table (user_id, friend_id, status). "Add friend" flow.
4. **Feed — pull mode first.** `SELECT * FROM posts WHERE author_id IN (friends) ORDER BY created_at DESC LIMIT 50`. Simple, works for <10k users.
5. **Feed — push mode (fanout-on-write).** When Alice posts, push post_id to each friend's `feed:{friend_id}` list in Redis. `GET /api/feed` reads the list, fetches posts.
6. **Likes + comments.** `likes` table, `comments` table. Redis counter for hot posts.
7. **Photos.** Upload to S3, store URL in `post.media_url`.
8. **Notifications.** When someone comments on your post, push a notification (socket or push).
9. **Search.** Index posts into Meilisearch; `GET /api/search?q=...`.
10. **Ranking.** Simple score: `score = likes*1 + comments*2 + recency_decay`. Order feed by score instead of time.

### 8.3 Small-scale architecture

```
   ┌────────────┐     ┌──────────────────────┐     ┌────────────┐
   │  Browser   │◀───▶│  Next.js (React)     │◀───▶│ PostgreSQL │
   │            │ HTTPS│  + Node/Express API  │ SQL │  users      │
   └────────────┘     │  - auth (JWT)        │     │  posts      │
                      │  - /feed /posts     │     │  friendships│
                      └────────────│─────────┘     └────────────┘
                                   │                 ┌────────────┐
                                   ├─── Redis ───────▶  feed index │
                                   │   (fanout lists) │  counters   │
                                   ▼                   └────────────┘
                      ┌──────────────────────┐
                      │  S3 (photos)          │
                      └──────────────────────┘
```

### 8.4 When you outgrow one box

- **Step 1:** Read replicas for Postgres.
- **Step 2:** Move feed index to Redis cluster.
- **Step 3:** Shard Postgres by `user_id`.
- **Step 4:** Move photos to a CDN.
- **Step 5:** Build TAO-like graph cache in front of Postgres.
- **Step 6:** Introduce ML ranking model.
- **Step 7:** Multiple datacenters with geo-DNS.

### 8.5 Estimated effort

- MVP (posts + friends + pull feed): **1 weekend.**
- + Push feed + likes/comments: **+1 weekend.**
- + Photos + notifications: **+1 weekend.**
- + Search + simple ranking: **+1 weekend.**

---

## 9. Key Design Decisions & Trade-offs

### 9.1 Fanout-on-write vs fanout-on-read

- **Choice:** Hybrid. Push for normal users, pull for celebrities.
- **Why:** Push gives O(1) read cost. But a celebrity's post would fan out to 50M feeds at once — a "thundering herd." Hybrid avoids both problems.
- **Trade-off:** Celebrity feeds are slightly slower (read-time pull), but the system stays stable.

### 9.2 Custom storage (Haystack, TAO) over off-the-shelf

- **Choice:** Build custom systems.
- **Why:** At 250B+ photos, filesystem metadata is the bottleneck; no off-the-shelf DB did graph + cache + MySQLdurability the way they needed.
- **Trade-off:** Massive engineering investment, but the upside is order-of-magnitude cost/perf wins that compound across billions of users.

### 9.3 Memcached as the #1 scaling lever

- **Choice:** Cache aggressively, treat cache misses as bugs.
- **Why:** Read:write ratio is ~100:1. The cache absorbs 99% of reads.
- **Tiered caching:** Not just "cache or DB" — tiered by temperature (hot/warm/cold data in different cache tiers).

### 9.4 Hack/HHVM over rewriting in Go/Rust

- **Choice:** Evolve PHP into Hack rather than rewrite.
- **Why:** Millions of lines of PHP, thousands of engineers trained in it. Rewriting would cost years.
- **Trade-off:** Hack is niche; hiring is harder than for Go/Java. But velocity was preserved.

### 9.5 ML ranking over chronological feed

- **Choice:** Rank stories by predicted engagement, not time.
- **Why:** Improves engagement materially (time spent, DAU). The ranking model is one of the most valuable ML models in the world.
- **Trade-off:** Loss of "fairness" or transparency; users don't see a chronological feed; hard to debug "why didn't I see X?"

### 9.6 GraphQL / BFF over monolithic API

- **Choice:** Mobile BFF (Backend-for-Frontend) that batches requests.
- **Why:** Mobile networks are slow; one round-trip beats ten. The BFF fans out internally and returns one composed response.
- **Trade-off:** BFF becomes a complex orchestrator; harder to debug.

---

## 10. Common Interview Questions

1. **How does the News Feed work?**
   Write path: post → store → fanout to friend feed indices. Read path: read candidate list → filter → rank with ML → fetch objects → return.

2. **Fanout-on-write vs fanout-on-read — which do you choose?**
   Hybrid. Push for normal users (cheap reads), pull for celebrities (avoid thundering herd).

3. **How would you design the like counter for a viral post?**
   Counter sharding in Redis. Split one logical counter into N shards; INCR a random shard; sum on read.

4. **How does Facebook store 250 billion photos?**
   Haystack. Append photos to large volume files; in-memory index maps photo-id → (volume, offset). Eliminates filesystem metadata overhead.

5. **What is TAO?**
   TAO is Facebook's distributed graph datastore. Objects (nodes) + associations (edges), cached in memcached, backed by MySQL. Optimized for the read-heavy social graph.

6. **How does the ranking model work?**
   Candidate generation → feature extraction (affinity, recency, type) → multi-task DNN scores each story on predicted engagement (like, comment, share, dwell). Top-N returned.

7. celebrity breaks fanout. What's the fix?
   Celebrity posts are not fanned out. When a user opens the app, the system pulls the celebrity's recent posts at read time (pull mode).

8. **Why did Facebook build its own photo storage?**
   Standard filesystems have too much per-file metadata overhead. At 250B photos, the FS metadata doesn't fit in RAM. Haystack eliminates it.

9. **How does Facebook cache the social graph?**
   Every object and association is cached in memcached. Graph queries (friends of friends) are broken into cached lookups. Cache hit rate is the key metric.

10. **How do they handle a datacenter going down?**
    Multi-datacenter replication. DNS failover to another region. Leader-follower DB topology with automated failover. Load is shed gracefully.

---

## Appendix: Further Reading

- "Scaling Memcache at Facebook" (NSDI 2013).
- "TAO: Facebook's Distributed Data Store for the Social Graph" (ATC 2013).
- "Finding a Needle in a Haystack: Facebook's Photo Storage" (OSDI 2010).
- Brakka et al., "TAO: The Power of the Social Graph."
- Huey & Nishtala et al., "Scaling Memcache at Facebook."
- Meta Engineering Blog.
- "HowHierarchical Memcached Reduced Tail Latency."

---

*End of Facebook system design.*

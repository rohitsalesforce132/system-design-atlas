# Instagram — System Design Atlas

> **Audience:** A developer transitioning to AI/ML engineering who wants to understand how Instagram is built end-to-end. Plain English, real numbers, ASCII diagrams, basics-first.

---

## 1. Overview & Scale Numbers

Instagram is a **photo-and-video-first social network** built around a ranked feed, Stories (ephemeral 24h content), Reels (short video), and Direct Messaging. It is famous in systems-design circles for one early decision: **running on a handful of technologies** — Python (Django), PostgreSQL, Redis, Cassandra — and scaling to 14M+ users before Facebook acquired them. That simplicity is the lesson.

### Scale (public numbers + estimates)

| Metric | Value | Why it matters |
|---|---|---|
| Monthly active users (MAU) | ~2 billion | Second-largest social network |
| Daily photos/videos shared | ~100+ million (posts + stories + reels) | Media-heavy workload |
| Stories DAU | ~1+ billion daily | Ephemeral content is a separate subsystem |
| Reels watched per day | ~billions | Short-video ranking is the new core |
| Likes per second (peak) | ~millions | Hot counter problem |
| Direct messages per day | ~billions | Real-time subsystem |
| Engineers at acquisition (2012) | ~13 | Famous "13 engineers, 30M users" story |
| Engineers today | thousands (within Meta) | Part of Meta's engineering org |
| Photos stored | hundreds of billions | Object storage dominates cost |
| Latency target | < 200ms feed load | Smooth UX |

### Why the numbers matter

Instagram is **media-heavy and read-heavy**. The dominant cost is storing and serving photos/videos. A single feed refresh fetches ~20 stories, each with a photo or video — that's 20 CDN requests. The feed ranking runs an ML model over thousands of candidate posts. The whole architecture is shaped around **making media cheap to store and fast to serve**, and **making feed reads fast**.

### The one-paragraph summary

Instagram is a **ranked feed of media posts** with three appendages bolted on: Stories (ephemeral), Reels (short-video ranking), and Direct (real-time chat). Posts are written to a DB and fanned out to follower feed indices; reads pull from those indices, rank candidates with ML, and fetch media from a CDN. Simplicity of stack (few technologies, used well) is the defining design choice.

---

## 2. High-Level Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                              USER DEVICES                                │
 │                iOS · Android · Web · Lite app                            │
 └──────────────────────────────────────────────────────────────────────────┘
                                  │  HTTPS / GraphQL
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                          EDGE / CDN LAYER                                │
 │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────┐ │
 │  │  PoP / Edge  │  │  Media CDN   │  │  TLS termination + Rate limit   │ │
 │  │  API gateway │  │  (photos/    │  │                                 │ │
 │  │  + auth      │  │   videos)    │  │                                 │ │
 │  └──────────────┘  └──────────────┘  └─────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                      APP SERVERS (Django / Python)                       │
 │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
 │   │ Feed Service │  │ Post Service │  │Story Service │  │Reels Service│ │
 │   │ (ranking)    │  │ (create)     │  │ (ephemeral)  │  │ (video rank)│ │
 │   └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
 │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
 │   │ Direct (DM)  │  │Notification  │  │  Search      │                  │
 │   │ Service      │  │ Service      │  │  Service     │                  │
 │   └──────────────┘  └──────────────┘  └──────────────┘                  │
 └──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                            STORAGE LAYER                                 │
 │  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌────────┐ ┌──────────────┐ │
 │  │ PostgreSQL │ │ Cassandra  │ │  Redis   │ │  S3    │ │Elasticsearch │ │
 │  │ (users,    │ │ (counters, │ │ (cache,  │ │ (media)│ │ (search)     │ │
 │  │  posts)    │ │  DMs)      │ │  feed)   │ │        │ │              │ │
 │  └────────────┘ └────────────┘ └──────────┘ └────────┘ └──────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

### Layered mental model

1. **Edge/CDN** — media is cached at the CDN edge; API requests go through a gateway.
2. **App servers** — Python/Django services. Stateless, horizontally scalable. Each subsystem (Feed, Story, Reels, Direct) is its own service.
3. **Storage** — PostgreSQL for core data, Cassandra for high-write data (feeds, counters, DMs), Redis for cache/feed-index, S3 for media.

---

## 3. Detailed Component Breakdown

### 3.1 Post Service

- **What:** Handles creating a new post (photo/video + caption + location + hashtags).
- **Why it exists:** The write path. Validates media, stores metadata, triggers fanout.
- **Flow:** Upload media → S3 → create `posts` row → trigger fanout to followers' feed indices.

### 3.2 Feed Service (the heart)

- **What:** Generates and ranks a user's home feed.
- **Why it exists:** With thousands of candidate posts per user, chronological is garbage. Ranking is essential.
- **Pipeline:** Pull candidates (from follower feed index + ads + recommended) → filter seen → rank with ML → fetch media URLs → return.

### 3.3 Story Service

- **What:** Ephemeral 24h content. Each story is a photo/video that disappears after 24h.
- **Why separate:** Different lifecycle (TTL-based), different ranking (most recent first, no deep ranking), different storage (time-bounded, can be aggressively garbage-collected).
- **Storage:** Cassandra with 24h TTL, or Redis with EXPIRE.

### 3.4 Reels Service

- **What:** Short-form video (15–90s) ranked by an engagement-prediction model.
- **Why separate:** Reels ranking is its own ML problem (watch time, completion rate, loop count) — different from feed ranking. Reels are served from a dedicated index and served like TikTok's For You Page.
- **Pipeline:** Candidate pool (followed creators + algorithmic recommendations) → rank by predicted watch time + engagement → infinite scroll.

### 3.5 Direct Messaging Service

- **What:** Real-time chat between users (1:1 or groups).
- **Why separate:** Real-time delivery, presence, read receipts — same shape as WhatsApp's problem, smaller scale per user.
- **Stack:** Cassandra for message storage, Redis for presence, WebSockets for delivery.

### 3.6 Notification Service

- **What:** Push notifications (likes, comments, follows, DMs).
- **Why it exists:** A like event needs to notify the post author without blocking the like itself.
- **Pattern:** Async fanout via queue. The like handler enqueues a notification job; a worker processes it and sends APNs/FCM.

### 3.7 Media Storage (S3 + CDN)

- **What:** Photos and videos stored in S3 (or Meta's internal equivalent), served via CDN.
- **Why:** Large blobs, write-once-read-many. CDN makes downloads fast globally.
- **Media pipeline:** Upload → store original → generate thumbnails (multiple sizes) → generate encoded video variants (multiple resolutions/bitrates) → push to CDN.

---

## 4. Data Model

### 4.1 PostgreSQL — core data

```sql
-- Users
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(64) UNIQUE,
    email         VARCHAR(255) UNIQUE,
    name          VARCHAR(255),
    avatar_url    TEXT,
    bio           TEXT,
    follower_count INT DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- Posts
CREATE TABLE posts (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT REFERENCES users(id),
    caption       TEXT,
    media_url     TEXT,           -- S3 key or CDN URL
    media_type    VARCHAR(16),    -- 'image' | 'video'
    location      TEXT,
    like_count    INT DEFAULT 0,
    comment_count INT DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_posts_user_id ON posts(user_id);
CREATE INDEX idx_posts_created ON posts(created_at DESC);

-- Follows
CREATE TABLE follows (
    follower_id   BIGINT REFERENCES users(id),
    followee_id   BIGINT REFERENCES users(id),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (follower_id, followee_id)
);
CREATE INDEX idx_follows_followee ON follows(followee_id);
```

### 4.2 Redis — feed index (the key to fast feeds)

```
   key:   feed:{user_id}
   value: ZSET of [post_id → score]   (score = creation timestamp)
```

- **ZSET (sorted set):** keeps posts ordered by timestamp. `ZREVRANGE` returns newest-first.
- **Fanout-on-write:** when Alice posts, `ZADD feed:{follower_id} {ts} {post_id}` for each follower.
- **Trim:** `ZREMRANGEBYRANK` to keep only the latest ~1000 posts per feed (older posts are pulled from DB if needed).

### 4.3 Cassandra — counters, DMs, large-scale time-series

```sql
-- Like counters (Cassandra, optimized for high-write counters)
CREATE TABLE like_counters (
    post_id    uuid,
    shard      int,          -- counter sharding for hot posts
    count      counter,
    PRIMARY KEY (post_id, shard)
);

-- Direct messages
CREATE TABLE messages (
    conversation_id  uuid,
    message_id       timeuuid,
    sender_id        uuid,
    ciphertext       blob,
    PRIMARY KEY ((conversation_id), message_id)
) WITH CLUSTERING ORDER BY (message_id DESC);
```

### 4.4 Why this database mix?

| Data | DB | Why |
|---|---|--- familiar, relational, ACID for core data |  |
| Feed index | Redis ZSET | Ordered, fast range queries, in-memory |
| Counters | Cassandra counters | High-write counters, sharded |
| Messages | Cassandra | Append-only, partition-local ordering |
| Media | S3 + CDN | Large blobs, cheap, fast global delivery |
| Search | Elasticsearch | Inverted index for hashtag/caption search |

*(Render note: first row "Why" = familiar, relational, ACID for core data.)*

### 4.5 Counter sharding (same idea as Facebook)

A viral Reel can get 10k+ likes/sec. One counter can't handle that. Split into N shards:

```
   like_count:{post_id}:0   →  1,233
   like_count:{post_id}:1   →  1,198
   ...
   like_count:{post_id}:99  →  1,255
   TOTAL = SUM
```

---

## 5. Request Flow — Posting a Photo and Seeing It in Feed

> **Alice posts a photo → Bob (her follower) opens Instagram 10 min later.**

### Write path (Alice posts)

```
 Alice's phone                  Instagram Backend
 ─────────────                  ─────────────────
      │
   1. Selects photo, writes caption
   2. POST /api/posts
      { caption, media_bytes }
      │
      ├──── 3. API gateway ────────▶ [Auth, rate limit]
      │
      │                       4. Post Service
      │                          a. Upload media to S3
      │                          b. Generate thumbnails (async)
      │                          c. Create row in posts (Postgres)
      │                          d. post_id assigned
      │
      │                       5. Fanout Service
      │                          a. Fetch Alice's followers
      │                          b. For each follower F:
      │                                ZADD feed:F {ts} {post_id}
      │                          c. (Celeb fallback: pull mode)
      │
      │◀─── 6. 200 OK ─────────────
      │
      │                       7. Async:
      │                          - index caption/hashtags for search
      │                          - notify mentioned users
      │                          - log event for ML training
```

### Read path (Bob opens the app)

```
 Bob's phone                    Instagram Backend
 ─────────────                  ─────────────────
      │
   1. Opens app
   2. GET /api/feed
      │
      ├──── 3. API gateway ────────▶ [Auth]
      │
      │                       4. Feed Service
      │                          a. ZREVRANGE feed:bob_id 0 999   → ~1000 candidate post_ids
      │                          b. Fetch post objects from Postgres/Redis cache
      │                          c. Filter: seen? blocked? deleted?
      │                          d. Rank with ML model (affinity, recency, engagement)
      │                          e. Top ~20 selected
      │                          f. Fetch media URLs (CDN-signed)
      │                          g. Compose response
      │
      │◀─── 5. JSON response ──────
      │     { stories: [ {post_id, media_url, caption, ...} ] }
      │
   6. For each post, fetch media from CDN
   7. Render feed
```

### Why this design works

- **Fanout-on-write** keeps feed reads cheap (just read a Redis ZSET).
- **Media on CDN** keeps media fetch off the origin servers.
- **Ranking is decoupled** from storage — the ML model can change without DB changes.
- **Celebrity fallback** prevents fanout explosions for users with millions of followers.

---

## 6. Scaling Strategy

### 6.1 The Instagram scaling story (the famous part)

In 2012, Instagram had **14M users running on 3 engineers and a handful of technologies**:

- **Django (Python)** for the app layer.
- **PostgreSQL** for core data.
- **Redis** for feed indices.
- **Cassandra** for counters and other high-write data.
- **S3 + CDN** for media.

They scaled by:
- **Sharding Postgres** by user_id once a single DB couldn't keep up.
- **Using Redis as a queue + cache + feed index** (one tool, many uses).
- **Cassandra for counters** — Postgres couldn't keep up with like counters at scale.
- **Keeping the stack simple** — fewer moving parts, easier to operate with few engineers.

### 6.2 Postgres sharding (the early-years trick)

When one Postgres instance couldn't handle the load, Instagram sharded it:

```
   user_id % N → shard N
   shard 0: users 0, N, 2N, ...
   shard 1: users 1, N+1, 2N+1, ...
   ...
```

Each shard is a separate Postgres instance. Cross-shard queries (e.g., "show feed" which pulls from many shards) are avoided by using Redis as the feed index — the feed index is global, the post objects are fetched per-shard.

### 6.3 Connection pooling with Pgbouncer

Each app server connects to Postgres through **Pgbouncer**, a connection pooler. This avoids Postgres's expensive per-connection process model.

### 6.4 Cassandra for high-write data

Like counters, DMs, and other append-only/high-write data moved to Cassandra, which is optimized for that workload. Postgres stayed for core relational data (users, posts, follows).

### 6.5 CDN for media

Photos and videos are pushed to a CDN after upload. Downloads hit the CDN edge, not the origin. This is what makes Instagram feel fast even on slow networks.

### 6.6 Async pipeline

Thumbnail generation, search indexing, notification fanout — all async via a job queue (Celery historically). The post API returns before these finish.

### 6.7 Connection pooling + read replicas

Postgres read replicas absorb read load. Writes go to the primary. Pgbouncer pools connections.

---

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend language | **Python (Django)** | Fast iteration, huge ecosystem, good for I/O-heavy apps |
| App framework | Django | Batteries-included, ORM, admin |
| Core DB | PostgreSQL | Relational, ACID, JSONB, mature |
| Cache / feed index | Redis | Ordered sets, pub/sub, fast |
| High-write DB | Cassandra | Append-only, counters, horizontal scale |
| Media storage | S3 + CDN | Large blobs, global delivery |
| Search | Elasticsearch | Inverted index for hashtags/captions |
| Job queue | Celery + Redis/RabbitMQ | Async thumbnail gen, notifications |
| Frontend | React (web) | Component model |
| Mobile | Native (iOS/Swift, Android/Kotlin) | Performance, platform features |
| Load balancing | Nginx / HAProxy | L7 routing, TLS termination |
| Monitoring | Sentry, statsd, graphite | Error tracking + metrics |

*(Render note: first row "Why" = fast iteration, huge ecosystem, good for I/O-heavy apps.)*

### Why Django/Python?

Instagram's famous choice. Python is easy to hire for, fast to iterate in, and has a huge ecosystem (Django's ORM, admin, ecosystem). The trade-off is raw performance — Python is slower than Go/Java/C++. Instagram mitigated this by:
- Running many app server processes (horizontal scale).
- Moving CPU-heavy work (image processing, ML) to C extensions or separate services.
- Keeping the data layer fast (Redis, Cassandra) so Python isn't the bottleneck.

The lesson: **developer velocity > raw backend performance** when you're small. You can always throw more boxes at a slow language; you can't throw boxes at slow development.

---

## 8. How YOU Can Build a Simplified Version

You don't need a team of 13 or Django. Here's a **weekend-scale Instagram clone**.

### 8.1 Tech choices

| Concern | Choice | Why |
|---|---|---|
| Protocol | HTTP/REST + WebSockets | Simple, ubiquitous, realtime if needed |
| Backend | Node.js + Express OR Python + FastAPI | Fast iteration |
| DB | PostgreSQL | Relational + JSONB |
| Cache / feed index | Redis | ZSET for feed ordering |
| Object storage | S3 | Photos/videos |
| Search | Meilisearch or Postgres FTS | Simple search |
| Frontend | React + Next.js | SSR, components |
| Hosting | Vercel (frontend) + Render/Railway (backend) | Easy deploy |

### 8.2 Build order

1. **Auth + User model.** Email/username, JWT sessions.
2. **Post + Profile.** `posts` table, `POST /api/posts`, `GET /api/users/:id`.
3. **Follows.** `follows` table. "Follow" flow.
4. **Feed — pull mode first.** `SELECT * FROM posts WHERE user_id IN (followees) ORDER BY created_at DESC LIMIT 50`.
5. **Feed — push mode (fanout-on-write).** When Alice posts, `ZADD feed:{follower_id} {ts} {post_id}` for each follower. `GET /api/feed` reads `ZREVRANGE feed:{user_id}`.
6. **Likes + comments.** `likes` table, `comments` table. Redis counter for hot posts.
7. **Photos.** Upload to S3, store URL in `post.media_url`.
8. **Notifications.** On like/comment, enqueue notification job → push to author.
9. **Search.** Index posts into Meilisearch; `GET /api/search?q=...`.
10. **Ranking.** Simple score: `score = likes*1 + comments*2 + recency_decay`. Order feed by score.

### 8.3 Small-scale architecture

```
 ┌────────────┐     ┌──────────────────────┐     ┌────────────┐
 │  Browser   │◀───▶│  Next.js (React)     │◀───▶│ PostgreSQL │
 │            │ HTTPS│  + Node/Express API  │ SQL │  users      │
 └────────────┘     │  - auth (JWT)        │     │  posts      │
                    │  - /feed /posts      │     │  follows    │
                    └────────────│─────────┘     └────────────┘
                                 │                 ┌────────────┐
                                 ├─── Redis ───────▶│ feed index │
                                 │   (ZSET)         │  counters   │
                                 ▼                  └────────────┘
                    ┌──────────────────────┐
                    │  S3 (photos)          │
                    └──────────────────────┘
```

### 8.4 When you outgrow one box

- **Step 1:** Read replicas for Postgres.
- **Step 2:** Move feed index to Redis cluster.
- **Step 3:** Shard Postgres by `user_id`.
- **Step 4:** Move photos to a CDN.
- **Step 5:** Move counters to Cassandra.
- **Step 6:** Introduce ML ranking model.
- **Step 7:** Multiple datacenters with geo-DNS.

### 8.5 Estimated effort

- MVP (posts + follows + pull feed): **1 weekend.**
- + Push feed + likes/comments: **+1 weekend.**
- + Photos + notifications: **+1 weekend.**
- + Search + simple ranking: **+1 weekend.**

---

## 9. Key Design Decisions & Trade-offs

### 9.1 Few technologies, used well (the Instagram philosophy)

- **Choice:** Django + Postgres + Redis + Cassandra. That's it.
- **Why:** Few engineers, need velocity. Each tool does multiple jobs (Redis = cache + queue + feed index).
- **Trade-off:** Each tool is pushed to its limit; operational expertise is concentrated. But you avoid the complexity of 20 different databases.

### 9.2 Fanout-on-write for feeds

- **Choice:** Push post IDs to follower feed indices in Redis.
- **Why:** Cheap reads. Feed read = one ZSET range query.
- **Trade-off:** Write amplification for popular users. Celebrity fallback to pull mode.

### 9.3 Postgres sharding by user_id

- **Choice:** Shard Postgres horizontally by `user_id`.
- **Why:** Keeps each user's data on one shard → simple queries.
- **Trade-off:** Cross-shard queries (global search) need a separate system (Elasticsearch).

### 9.4 Cassandra for counters

- **Choice:** Move like counters out of Postgres into Cassandra.
- **Why:** Postgres `UPDATE counters SET count=count+1` can't handle 10k QPS on a hot post. Cassandra counters + sharding can.
- **Trade-off:** Eventual consistency; counter may be slightly stale on read.

### 9.5 Django/Python over Go/Java

- **Choice:** Python for the app layer.
- **Why:** Developer velocity, hiring, ecosystem.
- **Trade-off:** Slower than Go/Java; mitigated by horizontal scale and pushing hot paths to C extensions or separate services.

### 9.6 Separate subsystems for Stories / Reels / Direct

- **Choice:** Each major feature is its own service with its own storage choices.
- **Why:** Different access patterns. Stories are TTL-bounded; Reels need video ranking; Direct needs real-time delivery. One-size-fits-all storage is an anti-pattern.
- **Trade-off:** More services to operate. But each is simple.

---

## 10. Common Interview Questions

1. **How does the Instagram feed work?**
   Write path: post → store → fanout to follower feed indices (Redis ZSET). Read path: read candidates → filter → rank → fetch media → return.

2. **How would you design the like counter for a viral post?**
   Counter sharding in Redis or Cassandra. Split one counter into N shards; INCR a random shard; sum on read.

3. **Fanout-on-write vs fanout-on-read — which do you choose?**
   Hybrid. Push for normal users (cheap reads), pull for celebrities (avoid thundering herd).

4. **How did Instagram scale with so few engineers?**
   Few technologies, each pushed to its limit. Django + Postgres + Redis + Cassandra. Horizontal scaling. Async pipelines for non-critical work.

5. **Why Cassandra for counters?**
   Postgres can't handle 10k+ writes/sec on a hot counter row. Cassandra counters + sharding can.

6. **How are photos stored and served?**
   Original to S3, generate thumbnails (multiple sizes), push all to CDN. Downloads hit CDN edge.

7. **How does Instagram handle a celebrity with 100M followers posting?**
   Celebrity posts are not fanned out. Followers' feeds pull celeb posts at read time (pull mode).

8. **Why Redis ZSET for the feed index?**
   Sorted set keeps posts ordered by timestamp; `ZREVRANGE` returns newest-first in O(log(N)+M).

9. **How would you rank the feed?**
   Candidate generation → feature extraction (affinity, recency, engagement signals) → ML model scores each → top-N returned. Modern Instagram uses multi-task DNN.

10. **How do Stories differ from posts architecturally?**
    Stories are TTL-bounded (24h), ranked by recency (not ML), stored in Cassandra with TTL or Redis with EXPIRE, aggressively garbage-collected.

---

## Appendix: Further Reading

- Instagram Engineering Blog — "Tuning Postgres for Instagram," "Sharding Postgres at Instagram," "Feeding the Feed."
- HighScalability: "Instagram Architecture" writeups.
- Lisa Koeman et al., "Scaling Instagram."
- Meta Engineering Blog (Instagram sections).

---

*End of Instagram system design.*

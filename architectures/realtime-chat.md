# Real-Time Chat Platform — Sample Architecture

> **Audience:** A developer who wants to design a WhatsApp/Slack-style real-time messaging system from scratch. Plain English, real numbers, ASCII diagrams, basics-first — analogies before advanced concepts.

---

## 1. Problem Statement & Requirements

### 1.1 What are we building?

A real-time messaging platform supporting **1:1 chat**, **group chat** (up to thousands of members), **presence** (online/offline/last-seen), **typing indicators**, **message history**, and **delivery status** (sent / delivered / read). Think WhatsApp, Slack, Discord, Telegram.

**The analogy:** Imagine a giant office building with millions of desks. Each desk has a **mailbox** (your message inbox) and a **desk phone** (your live connection). When Alice wants to send Bob a note:
1. She drops the note in the building's **mailroom** (message service).
2. The mailroom makes a **copy for Bob's mailbox** (persistence) and checks if Bob is at his desk.
3. If Bob's desk phone is on the hook (online), the mailroom **rings his phone instantly** (push over his live socket).
4. If Bob is away, the mailroom **leaves a sticky note on the building's bulletin board** (push notification) so Bob sees it when he returns.

The hard parts: (a) holding millions of "desk phones" open at once, (b) finding the right desk fast, (c) not losing mail if the mailroom catches fire, (d) making a single message reach 1,000 desks (group chat) without melting the mailroom.

### 1.2 Functional Requirements

| # | Requirement | Priority |
|---|---|---|
| F1 | 1:1 text messaging, real-time delivery | P0 |
| F2 | Group chat (up to 1,024 members) | P0 |
| F3 | Message history (scroll back months/years) | P0 |
| F4 | Delivery status: sent ✓, delivered ✓✓, read (blue) | P0 |
| F5 | Presence: online/offline + last seen | P0 |
| F6 | Typing indicators ("Alice is typing…") | P1 |
| F7 | Media: images, voice notes, files | P0 |
| F8 | Offline message delivery (push notification) | P0 |
| F9 | Message search (within a chat) | P1 |
| F10 | Read receipts optional / block contacts | P1 |
| F11 | Message deletion / edit (time-limited) | P2 |
| F12 | Reactions (emoji) | P2 |
| F13 | Multi-device sync (phone + web) | P1 |

### 1.3 Non-Functional Requirements

| Attribute | Target | Why |
|---|---|---|
| Delivery latency | p99 < 200 ms (online recipient) | Must feel instantaneous |
| Availability | 99.99% | Chat is mission-critical for users |
| Connections | 10 million concurrent (design target) | Drives the connection-server design |
| Message throughput | 1 million msgs/sec sustained | Matches WhatsApp-class scale |
| Durability | No message ever lost once ACKed to sender | Trust = retention |
| Ordering | Strict per-sender, per-conversation | Conversations must read sensibly |
| Consistency | Read-your-writes for sender; eventual for delivery receipts | Sender sees own msg immediately |
| Storage retention | Years of history, fast pagination | Users expect to scroll back |

### 1.4 Out of scope (for this design)

- Voice/video calls (separate WebRTC subsystem).
- End-to-end encryption key management (we note where it plugs in; full Signal Protocol is a separate concern).
- Bot/automation platform.
- Stories/status features.

---

## 2. Capacity Estimation

Designing for a **Slack-to-WhatsApp scale**: 100 million MAU, with a realistic concurrency profile.

### 2.1 Connections (the dominant cost)

| Metric | Assumption | Math | Result |
|---|---|---|---|
| MAU | — | — | **100M** |
| DAU | 40% of MAU | 100M × 0.40 | 40M |
| Concurrent online (peak) | 25% of DAU active at once | 40M × 0.25 | **10M concurrent connections** |
| Connections per server | Go: ~65k–100k; Erlang: 200k+ | — | size the fleet from this |

At ~100k connections per server, **10M concurrent connections → ~100 chat servers** (plus headroom → ~150). This is the primary fleet-sizing number. Memory per connection (~50 KB for buffers + state in Go) means each server needs ~5–10 GB RAM just for sockets.

### 2.2 Message throughput

| Metric | Math | Result |
|---|---|---|
| Messages/user/day | 50 (active chatterers) | — |
| Daily messages | 40M × 50 | **2B messages/day** |
| Avg msgs/sec | 2B / 86400 | **~23,000 msgs/sec (avg)** |
| Peak msgs/sec (4×) | 23k × 4 | **~92,000 msgs/sec (peak)** |
| Group fanout amplification | avg group size 8 | effective writes = 92k × 8 = **~740k mailbox writes/sec** |

**Key insight:** A single group message of "hi" to a 1,000-member group is 1 incoming message but 1,000 outbound pushes (and 1,000 mailbox writes if we store per-recipient). This fanout asymmetry dominates backend cost.

### 2.3 Storage

| Data | Size | Volume/day | Volume/year |
|---|---|---|---|
| Text messages (avg 200 B + metadata = ~1 KB) | 1 KB | 2B → 2 TB/day | **730 TB/yr (text only)** |
| Media messages (15% of msgs, avg 1 MB photo/voice) | 1 MB | 300M media/day → 300 TB/day | **~110 PB/yr** |
| Message metadata rows (Cassandra) | 500 B | 2B → 1 TB/day | 365 TB/yr |

Media dwarfs text by 100×. This drives the object-storage + CDN design.

### 2.4 Bandwidth

| Source | Math | Bandwidth |
|---|---|---|
| Text message egress | 92k msgs/s × 1 KB × 2 (delivered) | ~**180 MB/s** (1.5 Gbps) |
| Media egress | 300M/day × 1 MB | 300 TB/day → **~3.4 GB/s (27 Gbps avg)** |
| Peak media | 4× | ~**110 Gbps** |
| Presence heartbeats | 10M conns × 1 HB/30s × 100 B | ~33 MB/s (~270 Mbps) |

### 2.5 Compute

- **Connection servers:** ~150 boxes (socket holding) — CPU-light, RAM-bound.
- **Message routers / fanout workers:** scale with msg throughput; ~50 boxes.
- **Media upload/thumbnail workers:** bursty, autoscaled.
- **Push notification workers:** talk to APNs/FCM; ~20 boxes.

### 2.6 The number that matters most

**Concurrent connections, not QPS.** A chat system's first bottleneck is socket capacity (RAM + file descriptors), not request rate. Every design choice below serves this fact.

---

## 3. High-Level Architecture

```
                       ┌─────────────────────────────────────────────────┐
                       │            USER DEVICES                          │
                       │   iOS · Android · Web · Desktop                  │
                       └─────────────────────────────────────────────────┘
                                         │  WSS (WebSocket Secure) + TLS
                                         │  + APNs/FCM push (when offline)
                                         ▼
                       ┌─────────────────────────────────────────────────┐
                       │              EDGE / CONNECTION LAYER             │
                       │  ┌──────────┐ ┌──────────┐ ┌──────────┐         │
                       │  │ Chat Srv │ │ Chat Srv │ │ Chat Srv │  ...    │
                       │  │  (Go)    │ │  (Go)    │ │  (Go)    │ ~150    │
                       │  │ sockets  │ │ sockets  │ │ sockets  │  boxes  │
                       │  └────┬─────┘ └────┬─────┘ └────┬─────┘         │
                       │       │            │            │                │
                       │       └────────────┼────────────┘                │
                       │                    ▼                              │
                       │           L4 Load Balancer (HAProxy/ALB)         │
                       │           + sticky sessions / consistent hash    │
                       └─────────────────────────────────────────────────┘
                                         │
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
          ┌──────────────┐      ┌──────────────┐       ┌──────────────┐
          │   Presence   │      │  Message     │       │   Media      │
          │   Service    │      │  Router /    │       │   Service    │
          │  (Redis)     │      │  Fanout      │       │  (upload +   │
          │              │      │  (Go)        │       │   thumbnail) │
          └──────┬───────┘      └──────┬───────┘       └──────┬───────┘
                 │                     │                      │
                 │                     │ produce msg events   │ pre-signed URL
                 │                     ▼                      ▼
                 │              ┌──────────────┐       ┌──────────────┐
                 │              │    Kafka     │       │ Object Store │
                 │              │  (msg bus)   │       │  (S3) + CDN  │
                 │              └──────┬───────┘       └──────────────┘
                 │                     │
                 │       ┌─────────────┼──────────────┬───────────────┐
                 │       ▼             ▼              ▼               ▼
                 │  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
                 │  │ Persist │  │ Push     │  │ Receipt  │  │  Analytics   │
                 │  │ Worker  │  │ Worker   │  │ Worker   │  │  (ClickHouse)│
                 │  │ →Cass   │  │ →APNs/FCM│  │ →update  │  └──────────────┘
                 │  └────┬────┘  └──────────┘  │  status  │
                 │       │                     └────┬─────┘
                 │       ▼                          │
                 │  ┌────────────────────────────┐  │
                 │  │   Message Store            │  │ delivery/read receipts
                 │  │   (Cassandra / ScyllaDB)   │  │ flow back via Kafka
                 │  │   partition: (user_id)     │◀─┘
                 │  └────────────────────────────┘
                 │
                 │  ┌────────────────────────────┐
                 └─▶│  User / Profile Store      │
                    │  (PostgreSQL, sharded)     │
                    └────────────────────────────┘
```

### 3.1 Layered mental model

1. **Connection layer** — the only thing touching user sockets. Stateless-ish: holds the socket + per-connection state (last-ACKed message ID, device info). Pushes bytes; no business logic.
2. **Routing / services layer** — knows "where is user X?" and "deliver this to these N recipients." Stateless, horizontally scalable.
3. **Storage layer** — durable. Cassandra for messages (write-optimized, partitioned by recipient mailbox). PostgreSQL for user profiles (strong consistency for auth). S3 + CDN for media.
4. **Event bus (Kafka)** — decouples persistence, push, receipts, and analytics. Producers never block on consumers.

The critical design rule: **the connection layer never touches the database directly.** It only talks to the router/services, which talk to storage. This keeps sockets cheap and prevents a slow DB query from stalling a million connections.

---

## 4. Component Selection

### 4.1 Chat servers — Go (goroutines) or Erlang

**Why Go:** Each connection maps to a goroutine (~8 KB stack, grows as needed). Go's runtime schedules them across cores efficiently. A single box holds 65k–100k connections comfortably. Excellent stdlib for networking, easy to hire for, fast compile times.
**Alternatives:**
- *Erlang/OTP* — the WhatsApp choice; holds 200k+ connections per node via ~2 KB processes + supervision trees + hot code reload. Best-in-class for connection density, but smaller talent pool and steeper learning curve.
- *Java/Netty* — works, but JVM memory overhead per connection is higher (~50 KB+), so fewer connections per box.
- *Rust (tokio)* — excellent perf and memory, but ecosystem and team velocity considerations; overkill unless you're chasing the last 20% of density.

We choose Go for the balance of density, ecosystem, and team scalability. If we were WhatsApp-scale with a telecom-grade SLA, Erlang would win.

### 4.2 Message store — Cassandra (or ScyllaDB)

**Why:** Messages are append-only, written at extreme rate, partitioned by recipient, and read in time order. Cassandra's data model — partition key + clustering key — is a perfect fit:
```
PRIMARY KEY ((recipient_id), message_id)
WITH CLUSTERING ORDER BY (message_id DESC)
```
This means "load latest 50 messages for user X" is a single contiguous partition scan. Writes are linear (LSM tree), no index maintenance on the hot path. Horizontal scaling is native.

**Alternatives:**
- *PostgreSQL* — works to a point, but B-tree index maintenance under sustained 100k writes/sec becomes painful; sharding is manual.
- *MongoDB* — possible, but Cassandra's partition model is a cleaner fit for the mailbox access pattern.
- *DynamoDB* — managed, scales, but cost at this write volume is high and you lose operational control.
- *ScyllaDB* — Cassandra-compatible, C++ rewrite, 5–10× faster on the same hardware. Strong choice; we'd migrate to Scylla once Cassandra ops become painful.

### 4.3 User profile store — PostgreSQL (sharded)

**Why:** Auth, credentials, and account config need strong consistency (ACID). You cannot have eventual consistency on "is this password correct." Postgres gives us transactions, rich indexing, and JSONB for flexible profile fields.
**Sharding:** by `user_id` once a single primary can't keep up. Cross-shard queries (rare) go through a lookup service.

### 4.4 Presence — Redis

**Why:** Presence is a hot, ephemeral key-value: `presence:{user_id} → {status, last_seen}` with a short TTL. Redis gives sub-ms reads/writes, native TTL, and atomic updates. Heartbeats from online clients refresh the TTL every ~30s; if heartbeats stop, the key expires → offline.
**Alternatives:** a dedicated presence service in memory (more code); Riak (overkill); Cassandra (higher latency, not TTL-native in the same way).

### 4.5 Typing indicators & ephemeral events — Redis Pub/Sub

**Why:** Typing indicators are fire-and-forget; they should never be persisted. Redis Pub/Sub broadcasts to interested chat servers in <5 ms. Subscribers are the chat servers with at least one participant of the conversation online.
**Trade-off:** Pub/Sub has no durability — if a server is slow, it misses the event. That's fine for typing (better to drop than to lag).

### 4.6 Event bus — Kafka

**Why:**
- Decouples the message router from persistence/push/receipts/analytics workers.
- Replay capability (re-process last 24h of events to fix a bug).
- Backpressure: if the push worker is slow, Kafka buffers; the connection layer is unaffected.
- Ordering guarantees per partition (key by `conversation_id` for per-chat ordering).
**Alternatives:** RabbitMQ (great for task queues, weaker for replay/analytics); NATS (lighter, less mature ecosystem); Pulsar (powerful, more complex ops).

### 4.7 Media — S3 + CDN

**Why:** Media is the dominant bandwidth cost. S3 for durable storage; CDN (CloudFront/Cloudflare) for delivery. Uploads use pre-signed URLs so the chat backend never proxies media bytes — it only hands out tokens.
**Alternatives:** self-hosted MinIO (more ops); serving media through the chat server (terrible — would saturate the socket fleet).

### 4.8 Push notifications — APNs (Apple) + FCM (Google)

**Why:** When a recipient is offline, the only way to wake their app is a platform push. There's no alternative for iOS. We integrate both and abstract behind a single Push Service.

### 4.9 Protocol — WebSocket with a compact binary framing

**Why:** WebSocket is bidirectional, persistent, firewall-friendly (works over HTTP upgrade), and universally supported. We layer a small binary framing on top (length-prefixed messages) to keep packets tiny on slow mobile networks.
**Alternatives:** MQTT (lighter, pub/sub native — WhatsApp's historical choice); raw TCP + custom protocol (faster but more code, harder debugging); HTTP long-polling (fallback only; wasteful).

---

## 5. Database Schema Design

### 5.1 Message store (Cassandra)

```sql
CREATE TABLE messages_by_user (
    user_id          bigint,       -- the MAILBOX OWNER (recipient or sender's copy)
    conversation_id  bigint,       -- 1:1 or group id
    message_id       timeuuid,     -- monotonic + sortable (use timeuuid to avoid collisions)
    sender_id        bigint,
    message_type     tinyint,      -- 0=text 1=image 2=voice 3=file 4=system
    payload          blob,         -- encrypted text blob or media reference
    media_ref        text,         -- S3 key if media
    status           tinyint,      -- 0=sent 1=delivered 2=read  (per-recipient)
    created_at       timestamp,
    PRIMARY KEY ((user_id), conversation_id, message_id)
) WITH CLUSTERING ORDER BY (conversation_id ASC, message_id DESC);
```

**Design notes:**
- **Partition key `user_id`**: all of a user's messages live on the same node → fast mailbox reads.
- **Clustering by `conversation_id` then `message_id DESC`**: "load latest 50 in conversation C" is a contiguous slice.
- **Per-recipient storage**: we write the message once per recipient mailbox (fanout-on-write). This trades write amplification for blazing-fast reads (no JOIN, no fanout-on-read). For 1:1 chat that's 2 writes; for a 1,000-member group, 1,000 writes — mitigated by async fanout workers (§8).

**Delivery receipts table (Cassandra):**
```sql
CREATE TABLE message_receipts (
    conversation_id  bigint,
    message_id       timeuuid,
    recipient_id     bigint,
    delivered_at     timestamp,
    read_at          timestamp,
    PRIMARY KEY ((conversation_id), message_id, recipient_id)
);
```
Updates to `delivered_at`/`read_at` are idempotent UPSERTs.

### 5.2 Conversations (PostgreSQL — `messaging` db)

```sql
CREATE TABLE conversations (
    id              bigint PRIMARY KEY,
    type            smallint NOT NULL,   -- 0=direct 1=group
    title           varchar(256),        -- group name (null for 1:1)
    created_by      bigint,
    created_at      timestamptz DEFAULT now(),
    last_message_at timestamptz          -- for sorting conversation list
);
CREATE INDEX idx_conv_lastmsg ON conversations(last_message_at DESC);

CREATE TABLE conversation_members (
    conversation_id bigint REFERENCES conversations(id),
    user_id         bigint NOT NULL,
    role            smallint DEFAULT 0,  -- 0=member 1=admin
    joined_at       timestamptz DEFAULT now(),
    last_read_msg_id timeuuid,           -- for unread count
    PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX idx_members_user ON conversation_members(user_id);
```

### 5.3 User profiles (PostgreSQL — `users` db, sharded)

```sql
CREATE TABLE users (
    id            bigint PRIMARY KEY,
    phone_or_email varchar(128) UNIQUE,
    username      varchar(32) UNIQUE,
    display_name  varchar(128),
    avatar_ref    varchar(256),          -- S3 key
    public_key    blob,                  -- for E2E (Signal pre-key bundle)
    status        varchar(16) DEFAULT 'active',
    created_at    timestamptz DEFAULT now()
);

CREATE TABLE devices (              -- multi-device support
    user_id      bigint REFERENCES users(id),
    device_id    varchar(64),       -- platform-generated
    platform     smallint,          -- 0=ios 1=android 2=web 3=desktop
    push_token   varchar(256),      -- APNs/FCM token
    last_active  timestamptz,
    PRIMARY KEY (user_id, device_id)
);
```

### 5.4 Media metadata (PostgreSQL — `media` db)

```sql
CREATE TABLE media (
    media_id    bigint PRIMARY KEY,
    owner_id    bigint,
    sha256      char(64) UNIQUE,    -- dedup
    size_bytes  bigint,
    mime_type   varchar(64),
    object_key  varchar(256),       -- S3 key
    thumb_key   varchar(256),       -- CDN thumbnail
    created_at  timestamptz DEFAULT now()
);
```

### 5.5 Presence (Redis — no schema)

```
Key:    presence:{user_id}
Value:  { "status":"online", "last_seen":1721900000, "device":"android" }
TTL:    60s (refreshed by heartbeat every 30s)

Set:    presence:watchers:{user_id}    → list of users who should be notified of status changes
```

---

## 6. API Design

Client-facing API is **WebSocket events** (not REST) for real-time actions, with a small **REST surface** for history and media.

### 6.1 WebSocket event envelope

```json
{ "type": "message.send", "id": "m_123", "payload": { ... } }
```

Every event has a client-generated `id` for ACK/dedup.

### 6.2 Core events (client → server)

```
message.send
  { "conversation_id": 42, "type":"text", "payload":"<ciphertext>", "client_msg_id":"c_99" }
  → ACK: { "type":"message.ack", "client_msg_id":"c_99", "server_msg_id":"...", "ts":1721900000 }

typing.start     { "conversation_id": 42 }
typing.stop      { "conversation_id": 42 }
presence.ping    { }                      // heartbeat, every 30s
receipt.read     { "conversation_id": 42, "message_id":"..." }
```

### 6.3 Core events (server → client)

```
message.new      { "conversation_id":42, "message_id":"...", "sender_id":7, "payload":"...", "ts":... }
message.status   { "message_id":"...", "status":"delivered"|"read", "by":7 }
presence.update  { "user_id":7, "status":"online", "last_seen":... }
typing.update    { "conversation_id":42, "user_id":7, "typing":true }
```

### 6.4 REST endpoints (non-real-time)

```
GET /v1/conversations?cursor=...&limit=30
→ conversation list with last message preview + unread count

GET /v1/conversations/{id}/messages?before=<msg_id>&limit=50
→ paginated history (pulls from Cassandra by partition)

POST /v1/media/upload-url
  { "filename":"pic.jpg", "size":102400, "mime":"image/jpeg" }
→ { "upload_url":"https://s3.../presigned", "media_id":"...", "thumb_url":"..." }

GET /v1/users/{id}                      // profile lookup
POST /v1/conversations                  // create group
POST /v1/conversations/{id}/members     // invite
```

### 6.5 Idempotency

Every `message.send` carries `client_msg_id`. The server dedupes on this key (stored in Redis for 24h). A retry returns the same `server_msg_id`. This handles flaky mobile networks where the ACK is lost and the client resends.

---

## 7. Step-by-Step Request Flow — "Alice Sends 'hi' to Bob (1:1)"

```
 Alice's phone          Chat Backend                                          Bob's phone
 ──────────────         ────────────                                          ───────────
      │                       │                                                    │
  1. Alice types "hi"          │                                                    │
  2. (optional E2E)            │                                                    │
     app encrypts with         │                                                    │
     session key for Bob       │                                                    │
      │                        │                                                    │
      │── 3. WSS event ───────▶│ (Chat Server A, holding Alice's socket)            │
      │   message.send         │                                                    │
      │   {conv:42,            │                                                    │
      │    payload:"<cipher>"} │                                                    │
      │                        │                                                    │
      │                        │── 4. Auth check + rate limit                       │
      │                        │── 5. Generate server_msg_id (Hi/Lo or snowflake)    │
      │                        │── 6. Produce to Kafka (topic: messages,             │
      │                        │       key: conversation_id=42)                      │
      │                        │                                                    │
      │◀── 7. ACK ─────────────│  { server_msg_id, ts }   (single grey tick)        │
      │                        │                                                    │
      │                        │  ┌─── Kafka consumers (async, parallel) ─────┐     │
      │                        │  │                                            │     │
      │                        │  │  PERSIST WORKER:                            │     │
      │                        │  │   - write to Cassandra under BOTH           │     │
      │                        │  │     Alice's mailbox AND Bob's mailbox       │     │
      │                        │  │     (fanout-on-write)                       │     │
      │                        │  │                                            │     │
      │                        │  │  FANOUT/ROUTER WORKER:                      │     │
      │                        │  │   - look up Bob's connection: is he online? │     │
      │                        │  │   - if yes → find which Chat Server holds   │     │
      │                        │  │     Bob's socket (Redis: conn:{user_id})    │     │
      │                        │  │     → publish "deliver" to that server      │     │
      │                        │  │   - if no → enqueue push notification       │     │
      │                        │  │                                            │     │
      │                        │  │  ANALYTICS WORKER:                          │     │
      │                        │  │   - emit metrics to ClickHouse               │     │
      │                        │  └────────────────────────────────────────────┘     │
      │                        │                                                    │
      │                        │── 8. Chat Server B (holding Bob) pushes ───────────▶│
      │                        │   message.new { sender:Alice, payload:"<cipher>" }  │
      │                        │                                                    │
      │                        │                                                    │── 9. Bob's app
      │                        │                                                    │     decrypts,
      │                        │                                                    │     shows "hi"
      │                        │                                                    │
      │                        │◀── 10. message.status (delivered) ──────────────────│
      │                        │   (produced to Kafka → receipt worker →            │
      │                        │    Cassandra receipt row + push to Alice's server) │
      │                        │                                                    │
      │◀── 11. status update ──│  "delivered" (double grey tick)                    │
      │                        │                                                    │
      │                        │◀──── 12. receipt.read ─────────────────────────────│  (when Bob opens chat)
      │                        │                                                    │
      │◀── 13. status update ──│  "read" (blue ticks)                               │
```

### 7.1 Why split persist and fanout into separate workers?

The connection layer's job ends at step 7 (ACK to Alice). Everything else — durability, delivery, receipts — happens asynchronously via Kafka. This means:
- Alice gets her ACK in <50 ms (we only wait for Kafka produce, not for Cassandra write).
- If Cassandra is slow, delivery to Bob is **not** blocked (fanout worker reads from Kafka independently).
- If Bob's chat server is unreachable, the fanout worker retries or falls back to push.

This **decoupling is the single most important scalability decision** in the design. Synchronous persistence-on-the-request-path would cap throughput at the DB's write speed and tie socket lifetime to DB health.

### 7.2 Group chat: same flow, N-times fanout

For a group message, step 6 produces one event to Kafka, but the **fanout worker** writes N copies (one per member mailbox) and pushes to all online members. For large groups (1,024 members), we use **tiered fanout**: recently-active members get immediate push; inactive members get batched delivery + a single push notification. This prevents a single group message from creating a 1,024-way thundering herd.

---

## 8. Scaling Strategy

### 8.1 Connection scaling (the primary bottleneck)

| Technique | Effect |
|---|---|
| **One goroutine per connection** (Go) | Each socket is cheap (~8 KB stack); 100k conns/box |
| **L4 load balancer + consistent hashing** | Reconnecting users tend to land on the same server (local state) |
| **Connection registry in Redis** | `conn:{user_id} → server_id` lets any router find any user's current server |
| **Horizontal fleet** | Add chat servers linearly with concurrent connection growth |
| **Memory budgeting** | ~10 GB RAM/box for 100k sockets; size fleet accordingly |

**When to switch to Erlang:** if connection density becomes the dominant cost (WhatsApp-class 200k+/box), Erlang's ~2 KB processes + supervision trees justify the operational investment.

### 8.2 Message throughput scaling

| Bottleneck | Solution |
|---|---|
| Cassandra write throughput | Partition by `user_id`; add nodes linearly; tune consistency level (LOCAL_QUORUM for durability, LOCAL_ONE for speed where acceptable) |
| Kafka throughput | Partition by `conversation_id` (preserves per-chat ordering); add partitions + brokers |
| Fanout worker CPU (big groups) | Scale consumer group; tiered fanout for large groups; pre-compute "active member" lists |

### 8.3 Read scaling (history)

- Cassandra partitions by `user_id` → each user's history is local to a node set.
- Pagination via `message_id` cursor (no OFFSET; uses clustering key range scan).
- Old messages (90+ days) can be moved to colder storage tiers (cheaper disks); hot partition stays in RAM-backed cache.

### 8.4 Media scaling

- **Upload:** pre-signed S3 URLs; chat backend never proxies bytes.
- **Delivery:** CDN caches media at the edge; origin (S3) only sees cache misses.
- **Thumbnails:** generated at upload time by workers; stored alongside originals.
- **Dedup:** hash-based; if Alice and Bob send the same meme, store once.

### 8.5 Presence scaling

- Redis cluster, sharded by `user_id`.
- Heartbeats every 30s; TTL 60s.
- Presence *notifications* (telling Alice that Bob came online) are themselves fanned via Redis Pub/Sub to only the chat servers that have watchers for Bob. Don't broadcast presence globally — that's O(users²).

### 8.6 Multi-region

- GeoDNS routes users to the nearest data center.
- **Per-region connection fleets** (sockets must be local for latency).
- **Cross-region messaging:** messages cross regions via Kafka MirrorMaker; recipient's home region persists and delivers. Trade-off: cross-region adds 50–150 ms; acceptable for async chat, not for calls.

---

## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---|---|---|
| **Chat server crash** | All its sockets drop; users reconnect | Clients auto-reconnect with exponential backoff; consistent hashing routes them to a surviving server; missed messages fetched from Cassandra on reconnect (gap-fill by `last_msg_id`) |
| **Cassandra node down** | Some partitions unavailable | Replication factor 3; LOCAL_QUORUM tolerates 1 node down per replication factor; hint handoff for transient failures |
| **Kafka broker down** | Event buffering continues on replicas | Replication factor 3; consumers resume from last committed offset; at-least-once + idempotent consumers |
| **Redis (presence) down** | Presence flaps; routing lookups fail | Redis cluster with failover; degrade to "treat as online, attempt delivery" — safer than dropping messages |
| **Push gateway (APNs/FCM) down** | Offline users don't get notified | Retry queue; eventual delivery on next app open (clients fetch missed msgs) |
| **Network partition** | Split-brain | Cassandra uses quorum + hinted handoff; never write to a minority partition; connection servers in minority shed load |
| **Mobile client offline + ACK lost** | Duplicate message send | `client_msg_id` dedup (§6.5) — server returns same `server_msg_id` |
| **Thundering herd on big group** | Fanout overwhelms recipients | Tiered fanout; rate-limit per conversation; batch pushes |
| **Clock skew** | Message ordering glitches | Use `timeuuid` (server-assigned) for ordering, not client timestamps; NTP on all hosts |
| **Media upload interrupted** | Half-uploaded file | Multipart upload with resumable parts; client retries only missing parts |
| **Hot conversation (viral group)** | One partition overwhelmed | Re-key by `(conversation_id, bucket)` to spread load; or designate "large group" handling path |

### 9.1 The "no message lost" guarantee

Once the server ACKs a `message.send`, the message **will** be persisted and delivered. The path is: Chat Server → Kafka (durable, replicated) → Persist Worker → Cassandra (replicated). If any downstream consumer fails, Kafka retains the event for replay. The only acceptable loss is if the client never receives the ACK (network drops between steps 6 and 7) — in which case the client retries with the same `client_msg_id`, and dedup ensures correctness.

---

## 10. Trade-off Analysis

### 10.1 Fanout-on-write vs. fanout-on-read

- **Choice:** Fanout-on-write (write each message to every recipient's mailbox).
- **Why:** Reads are 10–100× more frequent than writes (scrolling history vs. sending). Optimizing for read latency pays off. Recipient mailbox reads are a single partition scan — no JOINs.
- **Cost:** Write amplification (1 group msg → 1,024 writes). For very large groups (Discord-style servers with 100k members), this breaks down → switch to fanout-on-read or a hybrid (write once, read-time fanout with caching).

### 10.2 Go vs. Erlang for connection servers

- **Choice:** Go.
- **Why:** Balance of connection density (100k/box is enough for our scale), ecosystem, hiring, and team velocity.
- **Cost:** Lower density than Erlang (need ~2× the boxes for the same connections); no built-in supervision trees (we build them); no hot code reload (we accept rolling restarts). At 10× our scale, revisit Erlang.

### 10.3 Per-recipient mailbox storage (denormalization)

- **Choice:** Store a copy of each message in each recipient's Cassandra partition.
- **Why:** Read performance (single partition scan per user) and deletion privacy (Alice deletes → only her copy goes; Bob's copy stays).
- **Cost:** Storage multiplication by group size; harder global search (must query across partitions — we use a separate search index for that).

### 10.4 Async fanout via Kafka vs. synchronous delivery

- **Choice:** Async (Kafka decouples persist/fanout from the connection layer).
- **Why:** Protects sockets from backend slowness; enables independent scaling; provides replay for bug recovery.
- **Cost:** Slight latency increase (Kafka hop adds 5–20 ms); eventual consistency on receipts; consumers must be idempotent. Worth it.

### 10.5 Cassandra vs. PostgreSQL for messages

- **Choice:** Cassandra.
- **Why:** Append-only workload, linear writes per partition, horizontal scaling, no index maintenance on hot path. PostgreSQL's B-trees would struggle at 100k writes/sec without heavy tuning.
- **Cost:** No secondary indexes (can't easily "search all messages for X" — needs a separate ES index); no multi-row transactions; tuning consistency levels requires expertise.

### 10.6 WebSocket vs. MQTT

- **Choice:** WebSocket with binary framing.
- **Why:** Universally supported, bidirectional, firewall-friendly; sufficient density for our scale.
- **Cost:** Slightly heavier than MQTT (which was designed for tiny packets on terrible networks). If our user base is predominantly low-end mobile on 2G/3G, MQTT's smaller wire footprint wins (WhatsApp's rationale).

### 10.7 Redis presence vs. database-backed presence

- **Choice:** Redis.
- **Why:** Sub-ms updates, native TTL, atomic ops. Presence is the hottest key in the system.
- **Cost:** Volatility (Redis crash = presence flap). Mitigated by cluster failover + graceful degradation (treat unknown as online-attempt-delivery).

### 10.8 At-least-once delivery vs. exactly-once

- **Choice:** At-least-once + idempotent consumers.
- **Why:** True exactly-once is prohibitively expensive across distributed systems (two-phase commit, distributed locks). At-least-once with `client_msg_id`/event-id dedup achieves the same user-visible result at a fraction of the cost.
- **Cost:** Every consumer must implement dedup; small storage overhead for the dedup keys.

---

## Appendix: Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| Client transport | WebSocket (WSS) + binary framing | Persistent, bidirectional, ubiquitous |
| Connection servers | Go (goroutines) | 100k sockets/box, good ecosystem |
| Load balancing | L4 (HAProxy/ALB) + consistent hashing | Efficient TCP distribution, sticky routing |
| Message router / fanout | Go services + Kafka consumers | Stateless, horizontally scalable |
| Event bus | Kafka | Decoupling, replay, ordering per partition |
| Message store | Cassandra (or ScyllaDB) | Write-optimized, partitioned by mailbox |
| User/profile store | PostgreSQL (sharded) | ACID for auth/config |
| Presence + typing | Redis + Pub/Sub | Sub-ms, TTL, ephemeral broadcast |
| Media | S3 + CDN (CloudFront/Cloudflare) | Offload bytes from chat fleet |
| Push | APNs + FCM via Push Service | Wake offline apps |
| Analytics | ClickHouse | OLAP on Kafka event stream |
| E2E encryption (optional) | Signal Protocol | Privacy; keys on devices |
| Orchestration | Kubernetes | Autoscale connection fleet by connection count |
| Observability | Prometheus + Grafana + Jaeger | Connection metrics are the SLO |

---

*End of Real-Time Chat architecture.*

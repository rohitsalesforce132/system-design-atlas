# WhatsApp — System Design Atlas

> **Audience:** A developer transitioning to AI/ML engineering who wants to understand how WhatsApp is built end-to-end. Plain English, real numbers, ASCII diagrams, basics-first.

---

## 1. Overview & Scale Numbers

WhatsApp is a **real-time messaging app** that handles chat, voice, and video. It is famous in the systems-design world because of a single fact: it ran 900 million monthly active users with only ~50 engineers. That ratio is a direct consequence of its technology choices (Erlang/OTP, FreeBSD, custom storage) and is one of the most studied "scale per engineer" stories ever published.

### Scale (current public numbers + conservative growth estimates)

| Metric | Value | Why it matters |
|---|---|---|
| Monthly active users (MAU) | ~3 billion | One of the largest apps on Earth |
| Daily messages sent | ~100 billion | Peak load is the design driver |
| Messages per second (sustained) | ~1.2 million | This is the throughput the backend is sized for |
| Peak messages/sec | ~5+ million | New Year's Eve / Diwali / midnight spikes |
| Media shared per day | ~7+ billion (photos, videos, voice notes) | Storage & bandwidth dominate costs |
| Voice/video minutes per day | ~15+ billion | Real-time media is the hardest subsystem |
| Connected (online) devices at once | ~200–300 million | Long-lived connections → connection servers are the bottleneck |
| Engineers | ~50 (historic) → hundreds today | Famous "WhatsApp only had 50 engineers" number |
| Servers | ~500 BSD servers at 900M MAU (historic), now many thousands | Erlang is extremely CPU-efficient |
| Latency target | < 200 ms p99 delivery for messages | Real-time feel |
| Reliability | "Message delivered, or the user knows" | No silent failures |

### Why the numbers matter

The dominant cost is **sustained connections**. Every online device holds a long-lived TCP+TLS socket to a WhatsApp server. That means RAM (for socket buffers and connection state) and file-descriptor capacity, not request throughput, are the primary capacity limits. WhatsApp's architecture is shaped around this fact: a single Erlang VM can hold hundreds of thousands of concurrent connections, which is why they chose Erlang in the first place.

### The two-word summary of the design

**Push, don't poll.** A device is always connected to a chat server over a long-lived socket. When a message arrives for it, the server pushes it immediately. If the device is offline, a small push notification (APNs/FCM) wakes the app and the app reconnects.

---

## 2. High-Level Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │              INTERNET (user devices)         │
                          │  Android · iOS · Web · KaiOS · Desktop       │
                          └─────────────────────────────────────────────┘
                                              │  TLS / WSS / MMS / SMS fallback
                                              ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                       EDGE / CONNECTION LAYER                          │
   │  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐ ┌──────────┐ │
   │  │ Chat Server   │  │ Chat Server   │  │ Chat Server  │ │  Media   │ │
   │  │ (Erlang/OTP)  │  │ (Erlang/OTP)  │  │ (Erlang/OTP) │ │  Server  │ │
   │  │ holds sockets │  │ holds sockets │  │ holds sockets│ │ (uploads)│ │
   │  └───────────────┘  └───────────────┘  └──────────────┘ └──────────┘ │
   └───────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                          ROUTING / DISPATCH                             │
   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ ┌─────────────┐ │
   │  │ Presence     │  │ Message      │  │ Group        │ │ Key-Exchange│ │
   │  │ Service      │  │ Router       │  │ Manager      │ │ (E2E keys)  │ │
   │  └──────────────┘  └──────────────┘  └──────────────┘ └─────────────┘ │
   └───────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                            STORAGE LAYER                                │
   │  ┌──────────────┐  ┌──────────────┐  └──────────────┐ ┌────────────┐ │
   │  │ Message      │  │ Media Store  │  │ User Profile  │ │ Counter    │ │
   │  │ Store        │  │ (object)     │  │ (MySQL → NewSQ│ │ Service   │ │
   │  │ (Cassandra)  │  │              │ │               │ │ (Hi/Lo)    │ │
   │  └──────────────┘  └──────────────┘  └──────────────┘ └────────────┘ │
   └───────────────────────────────────────────────────────────────────────┘
```

### Layered mental model

1. **Connection layer** — the only thing that touches user sockets. Pure push/fanout, no business logic. Each chat server is a stateless-py process per-connection Erlang process. State lives in ETS (in-memory) or delegated to the routing layer.
2. **Routing/dispatch layer** — knows "which server is user X currently connected to?" and "deliver this message to these N recipients." Stateless logic, highly available.
3. **Storage layer** — durable. Writes must survive crashes. Media is offloaded to object storage; metadata goes to databases.

---

## 3. Detailed Component Breakdown

### 3.1 Chat Servers (Connection Layer)

- **What:** A pool of Erlang/OTP nodes. Each node holds tens of thousands to hundreds of thousands of concurrent WebSocket/MQTI-like sockets. Each socket is one Erlang **process** (green thread, ~2 KB each).
- **Why it exists:** The cheapest way to hold N million connections is to have each connection be a tiny, cheap, isolated actor. Erlang processes cost a few KB, can be spawned in microseconds, and the BEAM scheduler distributes them across cores with preemptive scheduling.
- **Responsibility:** terminate TLS, authenticate the connection, maintain per-connection state (what messages have been ACKed by this device), push new messages, push receipts, handle keepalive pings.

### 3.2 Message Router

- **What:** A service that knows the mapping **user → currently-connected-chat-server** and fans a single message out to one or many recipients (for groups).
- **Why it exists:** A sender's chat server is not necessarily the same as the receiver's. Something must route "message from Alice to Bob's currently-connected server."
- **Responsibility:** lookup recipient server, deliver, retry if no server responds (recipient offline → enqueue + push), propagate delivery/read receipts back.

### 3.3 Presence Service

- **What:** Tracks "is user X online?" and "last seen time."
- **Why it exists:** The router needs to know if it should attempt immediate push or queue + notify. Presence also powers "last seen" UI.
- **Implementation pattern:** A sharded Redis cluster. Keys = `presence:{user_id}`, values = `{online:bool, last_seen:ts}` with short TTL. Online clients send heartbeats; absence of heartbeat flips to offline.

### 3.4 Group Manager

- **What:** Manages group metadata (name, members, admins) and group message fanout.
- **Why it exists:** Groups can have up to 1024 members. A single group message can fan out to 1024 devices. You need a service dedicated to this because naive fanout can create thundering-herd problems.
- **Responsibility:** membership lookups, group message delivery (often tiered: priority delivery to recently-active members), enforcing membership changes.

### 3.5 Media Server

- **What:** Handles file upload (chunked, resumable) and download of photos/videos/voice/docs.
- **Why it exists:** Media is far larger than text. You can't inline a 50 MB video into a chat message. The media server hands the client a pre-signed upload URL (S3-like), and once the upload completes, generates a small reference that gets attached to the chat message.
- **Responsibility:** chunked upload, deduplication (by hash), thumbnail generation, encryption-at-rest, download URLs with short TTLs.

### 3.6 Key Exchange / E2E Service

- **What:** Coordinates the Signal Protocol key exchange so messages are end-to-end encrypted.
- **Why it exists:** WhatsApp uses the Signal protocol. Keys live on devices. Servers only store a "key bundle" per device so a sender can encrypt a message for a recipient that is currently offline.
- **Responsibility:** publish pre-keys, validate identity, re-key after device changes.

### 3.7 Counter Service

- **What:** Generates monotonically increasing message IDs.
- **Why it exists:** Per-sender message ordering across devices (when a user switches from phone to web) requires strict ordering. WhatsApp historically used a **Hi/Lo** ID generator (one DB round-trip produces a range of IDs, exhausted locally before the next round-trip).
- **Pattern:** A sharded MySQL cluster allocated ID ranges, and chat servers consumed ranges locally.

### 3.8 Storage Layer

- **Message store:** Cassandra (historically) — append-only, partitioned by `(sender, timestamp)`. Tuned for writes.
- **Media store:** Object storage (custom / S3-like).
- **User profile / config:** MySQL (historically) → modern: NewSQL / distributed KV.
- **Counters (unreads):** Redis.

---

## 4. Data Model

### 4.1 Why these database choices?

| Data | Choice | Why |
|---|------|--- append-only, time-series-like, needs linear writes per partition, eventual consistency OK |  |
| User profile, credentials | MySQL (sharded) | Strong consistency needed for auth/config |
| Media blobs | Object storage | Large blobs, write-once-read-many, cheap |
| Presence, online status | Redis | Sub-ms reads, TTL-friendly |
| Counters (unread counts) | Redis | Atomic INCR, hot path |

*(Render note: the "Why" column text got merged into the header line above in some viewers — in plain reading: Cassandra for messages = append-only, time-series, linear writes per partition, eventual consistency OK; MySQL for profiles = strong consistency for auth/config; object storage for media = large blobs write-once-read-many; Redis for presence/counters = sub-ms reads, TTL-friendly, atomic INCR.)*

### 4.2 Cassandra message table (conceptual)

```sql
-- Cassandra CQL (denormalized, write-optimized)
CREATE TABLE messages (
    user_id           uuid,        -- the OWNER of this mailbox (recipient)
    message_id        timeuuid,    -- monotonic, sortable timestamp
    sender_id         uuid,
    conversation_id   uuid,
    encrypted_payload blob,        -- E2E encrypted blob
    media_ref         text,        -- pointer to object store, nullable
    status            tinyint,     -- 0=sent, 1=delivered, 2=read
    PRIMARY KEY ((user_id), message_id)
) WITH CLUSTERING ORDER BY (message_id DESC);
```

- **Partition key `user_id`:** all messages for one user live on the same node → fast mailbox reads.
- **Clustering key `message_id` (timeuuid):** sorts newest-first, so "load latest 50" is a contiguous disk read.
- **No UPDATEs:** status changes are appended as separate rows (or in a separate `receipts` table) to keep Cassandra's LSM tree happy.

### 4.3 User profile (MySQL, sharded)

```sql
CREATE TABLE users (
    id              BIGINT PRIMARY KEY,
    phone           VARCHAR(20) UNIQUE,
    username        VARCHAR(32) UNIQUE,
    name            VARCHAR(256),
    avatar_ref      VARCHAR(256),
    created_at      DATETIME,
    shard_id        INT           -- which MySQL shard holds this user
);
```

### 4.4 Media metadata (object store + small DB record)

```sql
CREATE TABLE media (
    media_id     BIGINT PRIMARY KEY,
    owner_id     BIGINT,
    sha256       CHAR(64) UNIQUE,   -- for dedup
    size_bytes   BIGINT,
    mime_type    VARCHAR(64),
    object_key   VARCHAR(256),      -- key in object store
    created_at   DATETIME
);
```

---

## 5. Request Flow — Sending a Message

> The canonical WhatsApp action: **Alice sends "hi" to Bob.**

```
  Alice's phone                WhatsApp Backend                         Bob's phone
  ─────────────                ─────────────────                        ───────────
       │                            │                                       │
   1. Alice types "hi"               │                                       │
   2. App encrypts with             │                                       │
      Signal session key            │                                       │
      (using Bob's pre-key)         │                                       │
       │                            │                                       │
       │──── 3. WSS msg ────────────▶│                                      │
       │     {to: Bob,              │                                       │
       │      ciphertext: ...}      │                                       │
       │                            │                                       │
       │                       4. Chat server authenticates                │
       │                       5. Gets message ID from Counter            │
       │                       6. Writes to Cassandra (Bob's mailbox)     │
       │                            │                                       │
       │                       7. Router looks up Bob's server            │
       │                            │                                       │
       │                            │──── 8. Push ciphertext ──────────────▶│
       │                            │                                       │
       │                            │                     9. Bob's app      │
       │                            │                        decrypts,     │
       │                            │                        shows "hi"    │
       │                            │                                       │
       │                            │◀──── 10. Delivery ACK ───────────────│
       │                            │                                       │
       │◀─── 11. Server ACK ─────────│                                      │
       │     (single grey tick)      │                                       │
       │                            │                                       │
       │                            │◀──── 12. Read receipt ───────────────│
       │                            │                                       │
       │◀─── 13. Read receipt ───────│                                      │
       │     (blue ticks)           │                                       │
```

### Step-by-step

1. **Local encrypt.** Alice's app already has a Signal session with Bob's device (negotiated via pre-key bundle). The plaintext "hi" is encrypted on-device; the server only ever sees ciphertext.
2. **Send.** App sends the ciphertext over its long-lived WebSocket to whichever chat server it's connected to.
3. **Chat server authenticates** the connection (already authenticated via long-lived session token).
4. **ID allocation.** Chat server asks Counter Service for a message ID (Hi/Lo range, so this is often a local counter, not a DB round-trip).
5. **Persist.** Chat server writes the ciphertext blob to Cassandra under **Bob's `user_id` partition** (so Bob's mailbox loads fast). Note: the server does not need to decrypt; it just stores ciphertext.
6. **Route.** Chat server asks Message Router: "which server is Bob connected to?" (or "is Bob online?").
7. **Push.** If Bob is online, router forwards ciphertext to Bob's chat server, which pushes it to Bob's socket. If Bob is offline, a push notification is sent (APNs/FCM) so Bob's app wakes up and fetches.
8. **Delivery ACK.** Bob's app decrypts, displays, and sends an ACK.
9. **Server ACK → Alice.** Alice's app shows a single grey tick (server received).
10. **Read receipt.** When Bob opens the chat, the app sends a read receipt. Alice's app shows two blue ticks.

### Why this design is clever

- **The server never sees plaintext.** E2E encryption means even WhatsApp engineers cannot read your messages.
- **Cassandra write is the critical path.** Everything else is best-effort fanout. Because Cassandra is write-optimized and partitioned by recipient, the write is fast and scales horizontally.
- **Group messages** repeat steps 6–7 for each member, with tiered fanout (recently active members get priority delivery).

---

## 6. Scaling Strategy

### 6.1 Connection scaling — the Erlang trick

WhatsApp's signature scale story: **one Erlang process per connection.** Erlang processes are not OS threads — they're green threads scheduled by the BEAM VM. Each costs ~2 KB and the scheduler is preemptive. So a single beefy box can hold **hundreds of thousands of concurrent sockets**. WhatsApp famously hit 2M concurrent connections on a single BSD box during testing.

```
   ┌───────────────────────────────────────┐
   │  One Erlang VM node                   │
   │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ │
   │  │proc 1│ │proc 2│ │proc 3│ │ ...  │ │  ← each proc = 1 user socket
   │  │(Alice)│ │(Bob)│ │(Cara)│ │      │ │     ~2 KB each, preemptive
   │  └──────┘ └------┘ └------┘ └------┘ │
   │  BEAM scheduler: N cores              │
   └───────────────────────────────────────┘
```

### 6.2 Sharding by user

Users are sharded across chat servers and databases. The **shard key is `user_id`**. All of a user's state — socket, mailbox, presence — lives on the same shard. This keeps hot data local and avoids cross-shard transactions.

### 6.3 Caching

- **Presence + last-seen:** Redis, TTL ~60s. Heartbeats refresh TTL.
- **Group metadata:** Redis cache in front of the group DB.
- **Media thumbnails:** CDN edge cache.

### 6.4 CDN for media

Photos/videos are pushed to a CDN after upload. When Bob downloads, he pulls from the edge closest to him — not from the origin. This is what makes media feel instant even on slow networks.

### 6.5 Load balancing at the edge

- **DNS-based Geo DNS** routes users to the nearest datacenter.
- **L4 load balancers** (HAProxy historically) distribute TCP connections across chat servers.
- **Consistent hashing** so that reconnecting users land near their state.

### 6.6 Handling spikes (New Year's Eve)

WhatsApp pre-provisions capacity for predictable spikes (midnight in each timezone). They also shed non-critical load (e.g., presence updates) during peaks to protect message delivery.

---

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend language | **Erlang/OTP** | Massive concurrency, hot code upgrade, supervision trees, decades of telecom pedigree |
| OS | **FreeBSD** (historically) | Stable, predictable networking stack, jails for isolation |
| Chat servers | Erlang/OTP nodes | One process per connection |
| Message DB | **Cassandra** | Linear writes, horizontal scale, per-partition ordering |
| Profile/config DB | **MySQL** (sharded) → modern: NewSQL / distributed KV | Strong consistency for auth |
| Cache | **Redis** | Presence, last-seen, unread counts |
| Object storage | Custom (S3-like) | Media blobs |
| Client crypto | **Signal Protocol** (Curve25519, AES, HMAC) | E2E encryption |
| Voice/video | **WebRTC** + custom media servers | Real-time audio/video |
| Protocol | Custom binary protocol over WebSocket (historically MQTT-like) | Tiny wire footprint |
| Web client | WebSocket bridged to phone (historically) | Phone was the source of truth |
| Load balancing | HAProxy, Geo DNS | L4 TCP distribution |
| Provisioning | Custom → modern: Kubernetes + Terraform | Infrastructure as code |

### Why Erlang? (the famous choice)

Erlang was built by Ericsson for telephone switches — systems with the exact shape of WhatsApp's problem: millions of concurrent connections, soft-real-time delivery, and "five-nines" uptime (99.999%). Erlang's **OTP supervision trees** mean that when one connection process crashes, only that connection dies; the rest keep running. Its **hot code upgrade** lets engineers push a new version of the chat server without dropping connections. No other mainstream language gave this combination in 2009, and few do today.

---

## 8. How YOU Can Build a Simplified Version

You don't need Erlang or billions of users to learn the patterns. Here's a **weekend-scale chat app** you can build.

### 8.1 Tech choices for the small version

| Concern | Choice | Why |
|---| realtime, bi-directional, ubiquitous |  |
| Lang/framework | Node.js + Socket.IO (or Go + gorilla/websocket) | Easy, lots of tutorials |
| DB | PostgreSQL (single instance) | Familiar, JSONB for flexibility |
| Cache | Redis (single instance) | Presence + pub/sub |
| Auth | JWT | Stateless |
| Frontend | React (web) | Simplest to iterate |
| Hosting | One VPS (DigitalOcean / Hetzner) | Cheap, full control |

*(Render note: the first row's label/why got merged in some viewers. It should read: Real-time transport = WebSocket — because it's real-time, bi-directional, ubiquitous.)*

### 8.2 What to build first (priority order)

1. **Auth + user table.** Phone-or-email signup, JWT issued.
2. **1:1 chat over WebSocket.** Server holds a `Map<userId, socket>`. On message, look up recipient socket, forward.
3. **Persistence.** Save every message to Postgres `messages` table. Reload history on login.
4. **Presence.** Redis `SET presence:{userId} online EX 60`. Heartbeat every 20s.
5. **Group chat.** `group_members` table. On group message, iterate members, forward to online sockets, persist for offline ones.
6. **Media.** Upload to S3 (or local disk), store reference in `media` table.
7. **Receipts.** Add `delivered_at` / `read_at` columns; emit socket events on status change.

### 8.3 Small-scale architecture

```
   ┌────────────┐     ┌──────────────────────────┐     ┌────────────┐
   │  Browser   │◀───▶│  Node.js + Socket.IO     │◀───▶│ PostgreSQL │
   │  (React)   │ WSS │  - in-memory socket map   │ SQL │  messages   │
   └────────────┘     │  - auth middleware (JWT)  │     └────────────┘
                      │  - event handlers         │     ┌────────────┐
                      └────────────│─────────────┘◀───▶│   Redis     │
                                   │   pub/sub         │  presence   │
                                   ▼                    └────────────┘
                      ┌──────────────────────────┐
                      │  Object storage (S3)      │
                      │  media uploads            │
                      └──────────────────────────┘
```

### 8.4 When you outgrow one box

- **Step 1:** Move the in-memory socket map to Redis pub/sub, so multiple Node servers can fan out to each other's sockets. Now you can run N Node boxes.
- **Step 2:** Shard Postgres by `user_id` once a single DB can't keep up.
- **Step 4:** Move messages to Cassandra or ScyllaDB (Cassandra-compatible, faster). Partition by `(recipient_id, timestamp)`.
- **Step 5:** Add a CDN for media.
- **Step 6:** Replace Socket.IO with raw WebSocket + a dedicated connection server (Go/Rust/Erlang) for connection density.

### 8.5 Estimated effort

- MVP (1:1 chat, no media): **1 weekend.**
- + Groups + media + receipts: **2–3 weekends.**
- + Multi-server fanout via Redis pub/sub: **+1 weekend.**
- + E2E encryption (Signal protocol libs): **+1–2 weekends.**

---

## 9. Key Design Decisions & Trade-offs

### 9.1 Erlang over Java/Go/C++

- **Choice:** Erlang/OTP.
- **Why:** Millions of cheap concurrent actors, supervision trees, hot code reload, telecom-grade reliability.
- **Trade-off:** Smaller talent pool, harder to hire, fewer libraries than JVM/Go ecosystems. WhatsApp accepted this because the concurrency model was the perfect fit.

### 9.2 E2E encryption by default

- **Choice:** Signal protocol, keys never leave devices.
- **Why:** Privacy, regulatory posture, user trust, lower liability for WhatsApp.
- **Trade-off:** No server-side search of message content, no smart server-side features (search, AI summaries) without client cooperation, complex multi-device sync.

### 9.3 Cassandra over MySQL for messages

- **Choice:** Cassandra for the message store.
- **Bigger focus:** MySQL for profiles.
- **Why:** Messages are append-only, partition-local ordering is enough, writes dominate, horizontal scaling is mandatory.
- **Trade-off:** No secondary indexes (can't easily "search all messages mentioning X"), no ACID across rows.

- **Note:** MySQL was retained for profiles because auth/config needs strong consistency. The split shows that **one database for everything is an anti-pattern at scale** — different data shapes need different engines.

### 9.4 Hi/Lo ID generation over central auto-increment

- **Choice:** Hi/Lo (allocate a range of IDs per server from a central DB, consume locally).
- **Why:** A central `AUTO_INCREMENT` would be a bottleneck and a single point of failure. Hi/Lo amortizes DB round-trips.
- **Trade-off:** IDs are not globally dense; gaps exist. Acceptable for messages.

### 9.5 Custom binary protocol over HTTP

- **Choice:** A compact binary protocol over WebSocket (historically MQTT-derived).
- **These decisions:** Tiny packets → less bandwidth on slow mobile networks (especially in developing countries, WhatsApp's growth market).
- **Trade-off:** Harder to debug, custom tooling needed.

### 9.6 FreeBSD over Linux

- **Choice:** FreeBSD with jails.
- **Why (historic):** Predictable, stable networking stack; WhatsApp engineers were familiar with it; jails gave cheap isolation.
- **Trade-off:** Less ecosystem support than Linux; over time WhatsApp moved toward Linux + containers as the industry standardized.

---

## 10. Common Interview Questions

1. **How does WhatsApp deliver messages in under 200ms at scale?**
   Long-lived WebSocket per device, in-memory socket map, Cassandra write-optimized mailbox, push if offline. The critical path is: encrypt → send → Cassandra write → fanout to online sockets.

2. **Why Erlang?**
   Millions of cheap green-thread processes, one per connection; supervision trees isolate failures; hot code upgrade without dropping sockets; telecom-grade reliability pedigree.

3. **How are messages ordered per chat?**
   Per-sender monotonic IDs (Hi/Lo) + per-recipient Cassandra partition sorted by `timeuuid`. Ordering is strict per sender, eventual across senders.

4. **How does WhatsApp handle a group of 1024 members?**
   Tiered fanout: recently-active members get immediate push; inactive members get queued delivery + push notification. The message is persisted once per recipient mailbox.

5. **How does E2E encryption work?**
   Signal protocol: each device generates a key pair; public keys are published via the key server; sender encrypts with recipient's public key + ephemeral keys; server only stores ciphertext.

6. **How would you scale from 1 user to 1 billion?**
   1 box → Redis pub/sub for multi-server fanout → shard Postgres by user → migrate messages to Cassandra → add CDN for media → add Geo DNS + multiple datacenters → consider Erlang/Go for connection density.

7. **What happens when a user is offline?**
   Message persisted to their Cassandra mailbox; APNs/FCM push sent; on reconnect, client fetches missed messages (pull after push).

8. **Why Cassandra and not MongoDB?**
   Cassandra's partition + clustering model gives linear writes per recipient mailbox with no index maintenance overhead; MongoDB's document model and indexing overhead is worse for pure append-only, high-throughput message workloads.

9. **How do they handle New Year's Eve spikes?**
   Pre-provision capacity per timezone, shed non-critical traffic (presence flapping), rate-limit non-essential features, graceful degradation.

10. **How are read receipts propagated?**
    A separate receipt event flows recipient → server → sender. The server updates a receipts row (or appends) and pushes the change to the sender's socket. Two blue ticks appear.

---

## Appendix: Further Reading

- Rick Reed's "Scaling to Millions of Simultaneous Connections" (Erlang Factory 2012) — the canonical WhatsApp talk.
- Facebook/Meta engineering blogs on WhatsApp voice/video scaling.
- Signal Protocol documentation (Open Whisper Systems).
- HighScalability: "WhatsApp Architecture" writeups.

---

*End of WhatsApp system design.*

# Databases — Complete Technology Guide

> Every database used across the System Design Atlas, explained from scratch.

---

## Table of Contents

1. [How to Choose a Database](#how-to-choose)
2. [Redis](#redis) — In-memory key-value store
3. [MySQL](#mysql) — Relational database (sharded)
4. [PostgreSQL](#postgresql) — Advanced relational database
5. [Cassandra](#cassandra) — Wide-column distributed database
6. [Amazon DynamoDB](#dynamodb) — Managed NoSQL
7. [Google Bigtable](#bigtable) — Petabyte-scale wide-column
8. [Elasticsearch](#elasticsearch) — Full-text search engine
9. [MongoDB](#mongodb) — Document database
10. [Amazon S3](#s3) — Object storage
11. [Google Spanner](#spanner) — Global ACID database
12. [ClickHouse](#clickhouse) — Columnar analytics database
13. [Snowflake](#snowflake) — Cloud data warehouse
14. [Neo4j](#neo4j) — Graph database
15. [SQLite](#sqlite) — Embedded database
16. [Comparison Table](#comparison-table)

---

<a id="how-to-choose"></a>
## How to Choose a Database

**Analogy:** A carpenter doesn't use a hammer for everything. Screws need screwdrivers, wood needs saws, pipes need wrenches. Similarly, no single database is best for everything. You pick based on your data shape and access pattern.

```
What's your data shape?

  Structured tables with relationships?
  → PostgreSQL or MySQL (relational)

  Massive scale, simple lookups by key?
  → Cassandra or DynamoDB (wide-column / key-value)

  Flexible JSON-like documents?
  → MongoDB (document)

  Need full-text search ("find me products like 'running shoes'")?
  → Elasticsearch (search engine)

  Need to traverse relationships (friends-of-friends)?
  → Neo4j (graph database)

  Need to store huge files (videos, images)?
  → S3 (object storage)

  Need sub-millisecond caching?
  → Redis (in-memory)

  Need analytics over billions of rows?
  → ClickHouse or Snowflake (columnar)

  Need global ACID transactions across regions?
  → Spanner (global relational)
```

---

<a id="redis"></a>
## Redis — In-Memory Key-Value Store

### What It Is (Analogy)

Think of Redis as **sticky notes on your desk**. Your notebook (database) has everything, but looking things up takes time. Sticky notes (Redis) hold the few things you need right now, instantly.

Redis stores all data in **RAM (memory)**, not on disk. Memory is 100x faster than disk, so reads take **0.1-1 milliseconds**.

### How It Works Internally

```
┌─────────────────────────────────────────────────┐
│                  REDIS SERVER                     │
│                                                  │
│  RAM (all data lives here):                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Key:foo  │ │ Key:bar  │ │ Key:baz  │       │
│  │ Value:   │ │ Value:   │ │ Value:   │       │
│  │ "hello"  │ │ 42       │ │ [l,i,s,t]│       │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│                                                  │
│  Single-threaded event loop:                    │
│  (no locks needed — one command at a time)      │
│                                                  │
│  RDB/AOF persistence (writes to disk            │
│  periodically for crash recovery)               │
└─────────────────────────────────────────────────┘
```

Redis is **single-threaded** for command execution. This sounds bad but is actually brilliant: no locks, no race conditions, no context switching overhead. One command at a time, insanely fast.

### Data Structures (This Is Why Redis Is Special)

Redis isn't just key-value. It supports rich data structures:

```
STRING:    SET user:1001 "Alice"          → Simple key-value
           INCR page_views                 → Atomic counter

HASH:      HSET user:1001 name "Alice"    → Like a mini-record
           HSET user:1001 age 30           (multiple fields per key)
           HGET user:1001 name             → "Alice"

LIST:      LPUSH messages "Hello"          → Ordered list
           RPUSH messages "World"          (push/pop from both ends)
           LRANGE messages 0 -1            → ["Hello", "World"]

SET:       SADD friends:1001 "Bob"         → Unordered unique set
           SADD friends:1001 "Carol"       (no duplicates)
           SINTER friends:1001 friends:1002 → Mutual friends

SORTED SET: ZADD leaderboard 100 "Alice"   → Set + score
            ZADD leaderboard 200 "Bob"      (automatically sorted)
            ZREVRANGE leaderboard 0 9       → Top 10 players

PUB/SUB:   PUBLISH channel "Hello"         → Message broadcasting
           SUBSCRIBE channel                (real-time notifications)
```

### Key Features

| Feature | Description |
|---------|------------|
| **Speed** | 100,000+ operations per second per instance |
| **Persistence** | RDB (snapshot) and AOF (append-only log) for crash recovery |
| **Replication** | Master-replica async replication |
| **Pub/Sub** | Built-in message broadcasting |
| **Lua Scripting** | Run atomic scripts server-side |
| **TTL** | Auto-expire keys (session data, cache invalidation) |
| **Cluster** | Auto-sharding across multiple nodes (hash slots) |
| **Streams** | Append-only log (like a mini-Kafka) |

### Delivery / Consistency Guarantees

```
Write to Master → Async replicate to Replicas

If you read from replica:
  → May get slightly stale data (eventual consistency)

If master crashes before replica catches up:
  → Last few writes could be lost (mitigated by AOF persistence)
```

### When to Use Redis

| ✅ Use Redis For | ❌ Don't Use Redis For |
|-----------------|----------------------|
| Caching (the #1 use) | Primary database (RAM is expensive) |
| Session storage | Large datasets (cost prohibitive) |
| Real-time leaderboards (sorted sets) | Complex queries / joins |
| Pub/Sub notifications | Data that must never be lost |
| Rate limiting (counters) | Ad-hoc analytics |
| Distributed locks | Blob storage (use S3) |
| Message queues (Redis Streams) | Heavy write volumes needing ACID |

### Real Companies Using Redis

| Company | How They Use Redis |
|---------|-------------------|
| **Twitter** | Timeline generation — pre-computed feeds stored in Redis lists |
| **Instagram** | Feed, notifications, story pre-computation |
| **Stack Overflow** | Cache for questions, answers, user profiles |
| **GitHub** | Pub/Sub for real-time notifications |
| **Tinder** | Sorted sets for "people near you" (geo + scoring) |
| **Zomato** | Real-time order tracking, restaurant availability, session cache |
| **PhonePe** | Rate limiting, idempotency keys, UPI request caching |

### Alternatives to Redis

| Alternative | When to Choose Instead |
|------------|----------------------|
| **Memcached** | Simpler, multi-threaded, pure key-value, no data structures |
| **Hazelcast** | When you need distributed in-memory compute grid |
| **Aerospike** | When dataset exceeds RAM — flash-optimized, still very fast |
| **Dragonfly** | Redis-compatible, multi-threaded, 25x faster on multi-core |

---

<a id="mysql"></a>
## MySQL — Relational Database (Sharded)

### What It Is (Analogy)

MySQL is like a **filing cabinet with strict rules**. Every document (row) goes into a specific folder (table), and every folder has a specific shape (schema). You can connect documents across folders using references (foreign keys).

### How It Works

```
┌─────────────────────────────────────────┐
│              MySQL Server                │
│                                         │
│  ┌──────────┐  ┌──────────┐            │
│  │ Table:   │  │ Table:   │            │
│  │ users    │  │ orders   │            │
│  │─────────│  │─────────│            │
│  │ id (PK)  │◄─│ user_id  │ (FK)       │
│  │ name     │  │ id (PK)  │            │
│  │ email    │  │ amount   │            │
│  └──────────┘  │ status   │            │
│                └──────────┘            │
│                                         │
│  Storage Engine: InnoDB (default)       │
│  - ACID transactions                   │
│  - Row-level locking                   │
│  - Crash recovery (redo log)           │
│  - Buffer pool (RAM cache for data)    │
└─────────────────────────────────────────┘
```

### Key Features

| Feature | Description |
|---------|------------|
| **ACID Transactions** | Atomic, Consistent, Isolated, Durable — safe for financial data |
| **Joins** | Combine data across tables (INNER, LEFT, RIGHT, FULL) |
| **Foreign Keys** | Enforce referential integrity |
| **Indexes** | B-tree indexes for fast lookups, full-text for search |
| **Replication** | Master-replica async replication |
| **InnoDB Engine** | Row-level locking, crash recovery, MVCC |

### How Companies Shard MySQL

MySQL was designed for a single machine. At Facebook/Flipkart scale, you shard:

```
Application
  │
  ▼
┌─────────────────────┐
│  Sharding Layer     │   (determines which shard)
│  shard = user_id % N│
└──────┬──────┬───────┘
       │      │
       ▼      ▼
  ┌────────┐ ┌────────┐ ┌────────┐
  │Shard 0 │ │Shard 1 │ │Shard N │
  │MySQL-A │ │MySQL-B │ │MySQL-C │
  │Master  │ │Master  │ │Master  │
  │+Replica│ │+Replica│ │+Replica│
  └────────┘ └────────┘ └────────┘
```

**Facebook runs thousands of MySQL shards.** Each shard holds a few million users.

### When to Use MySQL

| ✅ Use MySQL For | ❌ Don't Use MySQL For |
|-----------------|----------------------|
| Transactional data (orders, payments) | Unstructured/flexible schema data |
| E-commerce, banking, ERP | Massive-scale simple key lookups |
| Complex joins and relationships | Time-series data at scale |
| Apps with well-defined schema | Globally distributed writes |

### Companies Using MySQL

| Company | How |
|---------|-----|
| **Facebook** | Thousands of MySQL shards (user data, posts, comments) |
| **Flipkart** | Sharded MySQL for orders, products, users |
| **Twitter** | MySQL (sharded) for tweets, user data |
| **WordPress.com** | Millions of MySQL databases for blogs |
| **Booking.com** | Large MySQL deployment |

### Alternatives

| Alternative | When to Choose |
|------------|---------------|
| **PostgreSQL** | When you need advanced queries (JSON, GIS, full-text) |
| **Vitess** | When you need MySQL sharding without app changes (YouTube uses this) |
| **CockroachDB** | When you need global ACID without manual sharding |
| **TiDB** | MySQL-compatible, horizontally scalable |

---

<a id="postgresql"></a>
## PostgreSQL — Advanced Relational Database

### What It Is (Analogy)

PostgreSQL is like MySQL's **smarter older sibling**. Same filing cabinet concept, but this one can also handle JSON documents, geographic data, full-text search, and time-series — all in one system.

### How It Differs from MySQL

| Feature | MySQL | PostgreSQL |
|---------|-------|-----------|
| JSON support | Basic | Advanced (JSONB — indexable, queryable) |
| Full-text search | Basic | Advanced (tsvector, ranking) |
| Geospatial (GIS) | No | PostGIS extension (best-in-class) |
| Materialized views | No | Yes (pre-computed query results) |
| Concurrency model | Simple | MVCC (multi-version concurrency — readers don't block writers) |
| Data types | Standard | Custom types, arrays, ranges, UUID, IP addresses |
| Extensibility | Limited | Extensions (PostGIS, pgvector for AI, TimescaleDB) |

### Key Features

```
PostgreSQL's superpower is EXTENSIBILITY:

  ┌──────────────────────────────────┐
  │         PostgreSQL Core           │
  │──────────────────────────────────│
  │  Standard SQL + ACID + MVCC     │
  │──────────────────────────────────│
  │  Extension Layer:                │
  │  ┌─────────┐ ┌─────────┐        │
  │  │ PostGIS │ │pgvector │        │
  │  │(geo/GIS)│ │(AI/ML)  │        │
  │  └─────────┘ └─────────┘        │
  │  ┌─────────┐ ┌─────────┐        │
  │  │Timescale│ │pg_cron  │        │
  │  │(time-   │ │(scheduled│       │
  │  │ series) │ │  jobs)  │        │
  │  └─────────┘ └─────────┘        │
  └──────────────────────────────────┘
```

### Companies Using PostgreSQL

| Company | How |
|---------|-----|
| **Instagram** | PostgreSQL for user data, media metadata |
| **Uber** (originally) | PostgreSQL for trips, payments (later moved some to SchemaRDD) |
| **Spotify** | PostgreSQL for catalog, user playlists |
| **Razorpay** | PostgreSQL for transactions, merchants, settlements |
| **Zomato** | PostgreSQL for restaurants, orders, users |
| **Discord** | PostgreSQL for messages, users, servers |

### When to Use PostgreSQL

| ✅ Use PostgreSQL For | ❌ Don't Use PostgreSQL For |
|----------------------|---------------------------|
| Complex queries (JSON + SQL + GIS) | Simple key-value (use Redis) |
| Geospatial apps (maps, delivery zones) | Massive-scale unstructured data |
| Apps needing both SQL and NoSQL | When you need Cassandra-level write throughput |
| AI/ML (pgvector for embeddings) | Simple apps where MySQL suffices |
| Financial transactions | |

---

<a id="cassandra"></a>
## Cassandra — Wide-Column Distributed Database

### What It Is (Analogy)

Imagine a **notebook where every page can have different columns**, and you can tear the notebook into pieces and give each piece to a different person. Everyone can read/write independently, and the notebook never goes down because there are always copies.

Cassandra is designed for **massive write throughput** and **zero downtime**. No single point of failure — every node is equal.

### How It Works Internally

```
Cassandra's Architecture (Peer-to-Peer, No Master):

  Node 1 ◄──► Node 2
    ▲            ▲
    │            │
  Node 4 ◄──► Node 3

  (No master! All nodes are equal.
   If any node dies, others handle its data.)
```

### Data Model

```
Cassandra uses a "Keyspace → Column Family → Row → Column" model:

  Keyspace: my_app (like a database)
    │
    ├── Column Family: users (like a table)
    │     │
    │     ├── Row Key: user:1001
    │     │     ├── name: "Alice"
    │     │     ├── email: "alice@email.com"
    │     │     └── age: 30
    │     │     (can have different columns per row!)
    │     │
    │     ├── Row Key: user:1002
    │     │     ├── name: "Bob"
    │     │     ├── email: "bob@email.com"
    │     │     └── phone: "555-1234"    ← different column!
    │     │
    │     └── ...
```

### Partitioning (Consistent Hashing)

```
Data distribution via consistent hashing ring:

         0
       /    \
   Node A   Node B         Each node owns a range of the ring.
      |        |            Data is hashed → placed on the ring →
   Node D   Node C          stored by the node that owns that range.

  When a node joins/leaves:
  → Only its neighbors' data is redistributed
  → No massive data migration
```

### Replication

```
Replication Factor = 3 means data is stored on 3 nodes:

  Write to Node A → Node A replicates to Node B and Node D
  If Node A dies → Node B and Node D still have the data
  If Node A comes back → it syncs missed writes (hinted handoff)
```

### Key Features

| Feature | Description |
|---------|------------|
| **Decentralized** | No master node — all nodes equal. No single point of failure. |
| **Linear scalability** | Add nodes → get linear increase in throughput |
| **Tunable consistency** | ONE, QUORUM, ALL — choose per query |
| **High write throughput** | Optimized for writes (append-only, no reads needed) |
| **Multi-datacenter** | Built-in cross-DC replication |
| **Time-series** | Excellent for time-ordered data (metrics, IoT, activity logs) |

### Tunable Consistency

```
Write/Read consistency levels:

  ONE:       Write to 1 node, respond success.
             (Fast, but data might not survive node crash)

  QUORUM:    Write to majority (>50%) of replicas.
             (Stronger guarantee — data survives single node failure)

  ALL:       Write to ALL replicas.
             (Strongest guarantee, but slowest)

  LOCAL_QUORUM: Quorum within local datacenter.
             (Good for multi-DC setups)
```

### When to Use Cassandra

| ✅ Use Cassandra For | ❌ Don't Use Cassandra For |
|---------------------|--------------------------|
| Time-series data (metrics, logs, IoT) | Complex joins (not supported) |
| Write-heavy workloads | Ad-hoc analytical queries |
| Always-on availability (99.999%) | Transactional data (no ACID) |
| Multi-datacenter replication | Small datasets (overkill) |
| User activity logs | Apps with changing query patterns |

### Companies Using Cassandra

| Company | How |
|---------|-----|
| **Netflix** | Viewing history for 260M+ subscribers |
| **Instagram** | User activity, story data |
| **Twitter** | Timeline data |
| **Apple** | 100,000+ Cassandra nodes (largest deployment) |
| **Spotify** | User listening history, playlists metadata |
| **Reddit** | Votes, comments, post metadata |

---

<a id="dynamodb"></a>
## Amazon DynamoDB — Managed NoSQL

### What It Is (Analogy)

DynamoDB is Cassandra **but managed by AWS**. You don't install, configure, or maintain servers. You just say "I need a table with this key" and AWS handles everything — sharding, replication, scaling, backups.

### Key Concept: Provisioned vs On-Demand

```
PROVISIONED (Plan ahead):
  "I need 10,000 writes/sec and 50,000 reads/sec"
  → AWS allocates capacity. Cheaper if predictable.

ON-DEMAND (Pay per use):
  "Just handle whatever traffic comes"
  → AWS auto-scales instantly. More expensive per request,
    but no capacity planning needed.
```

### Data Model

```
DynamoDB Table:

  Partition Key (PK): user_id     ← Determines which partition
  Sort Key (SK): timestamp         ← Orders within partition

  ┌─────────────┬────────────┬──────────┬───────────┐
  │ user_id (PK)│ timestamp  │ event    │ data      │
  │             │ (SK)       │          │           │
  ├─────────────┼────────────┼──────────┼───────────┤
  │ user:100    │ 1706000000 │ login    │ {ip:...}  │
  │ user:100    │ 1706000120 │ click    │ {url:...} │
  │ user:100    │ 1706000300 │ purchase │ {item:...}│
  └─────────────┴────────────┴──────────┴───────────┘

  Query: "Get all events for user:100 after timestamp X"
  → Single partition read. Very fast.
```

### Features

| Feature | Description |
|---------|------------|
| **Single-digit ms latency** | At any scale — 10 items or 10 trillion |
| **Auto-scaling** | Handles traffic spikes automatically |
| **Global Tables** | Multi-region replication with single config |
| **TTL** | Auto-delete items after timestamp |
| **Streams** | CDC (change data capture) — trigger Lambda on changes |
| **DAX** | In-memory acceleration (microsecond latency) |
| **Point-in-time recovery** | Restore to any second in last 35 days |

### When to Use DynamoDB

| ✅ Use DynamoDB For | ❌ Don't Use DynamoDB For |
|---------------------|--------------------------|
| Serverless apps (Lambda) | Complex queries (joins) |
| Simple key-based lookups at scale | Apps not on AWS |
| Gaming leaderboards | When you need full-text search |
| Session management | When cost is critical at low scale |
| IoT event ingestion | When you need multi-item transactions |

### Companies Using DynamoDB

| Company | How |
|---------|-----|
| **Amazon** | Shopping cart, product catalog |
| **Netflix** | User preferences (supplementary to Cassandra) |
| **Airbnb** | Booking data |
| **Supercell** | Game state for Clash of Clans |
| **Duolingo** | User progress, lesson data |

---

<a id="bigtable"></a>
## Google Bigtable — Petabyte-Scale Wide-Column

### What It Is (Analogy)

Bigtable is Google's version of Cassandra — but built for **petabyte scale** with consistent low-latency. It's the database Google built for its own products (Search, Maps, YouTube).

### Key Design

```
Bigtable Architecture:

  ┌─────────────────────────────────────────┐
  │             Client App                   │
  └──────────────┬──────────────────────────┘
                 │
  ┌──────────────▼──────────────────────────┐
  │           Proxy Layer                    │
  └──────────────┬──────────────────────────┘
                 │
  ┌──────────────▼──────────────────────────┐
  │     Tablet Servers                       │
  │  ┌─────────┐ ┌─────────┐ ┌─────────┐   │
  │  │ Tablet 0│ │ Tablet 1│ │ Tablet 2│   │
  │  │ (rows   │ │ (rows   │ │ (rows   │   │
  │  │  a-m)   │ │  n-s)   │ │  t-z)   │   │
  │  └─────────┘ └─────────┘ └─────────┘   │
  └─────────────────────────────────────────┘
        │              │           │
  ┌─────▼─────┐ ┌─────▼─────┐ ┌──▼──────┐
  │  Colossus  │ │ Colossus  │ │Colossus │
  │  (Google's │ │  (Google's│ │(Google's│
  │  file sys) │ │  file sys)│ │file sys)│
  └───────────┘ └───────────┘ └─────────┘
```

### Companies Using Bigtable

| Company | How |
|---------|-----|
| **Google Search** | Web index storage |
| **Google Maps** | Map tile storage, route data |
| **YouTube** | Video metadata, analytics |
| **Snapchat** | Story data |
| **Twitter** (early) | Timeline storage |

### When to Use Bigtable

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Massive throughput (millions of ops/sec) | Small datasets |
| Time-series at petabyte scale | Complex queries |
| Google Cloud-native apps | Apps not on GCP |

---

<a id="elasticsearch"></a>
## Elasticsearch — Full-Text Search Engine

### What It Is (Analogy)

Imagine looking for a book in a library. Without a card catalog, you'd check every book one by one (slow). With a card catalog (index), you search "books about cooking" and instantly get results. **Elasticsearch is the card catalog for your data.**

It doesn't just match exact words — it understands:
- **"shoes"** matches "shoe", "running shoes", "shoe sale"
- **"colour"** matches "color" (synonyms)
- **"smart phone"** matches "smartphone" (compounds)
- **Ranking:** More relevant results first (TF-IDF / BM25 scoring)

### How It Works

```
Document In → ANALYZED → Inverted Index

Original: "The red running shoes are amazing"

ANALYZER steps:
  1. Lowercase: "the red running shoes are amazing"
  2. Remove stopwords: "red running shoes amazing"
  3. Stemming: "red run shoe amaze"
  4. Add to inverted index:

INVERTED INDEX:
  Term       → Document IDs
  ─────────────────────────────
  "red"      → [doc1, doc5, doc12]
  "run"      → [doc1, doc3, doc7, doc12]
  "shoe"     → [doc1, doc2, doc5, doc8, doc12]
  "amaze"    → [doc1, doc9]

Search "red shoes" → Intersect [doc1,doc5,doc12] ∩ [doc1,doc2,doc5,doc8,doc12]
                   → [doc1, doc5, doc12] → Rank by relevance score
```

### Key Features

| Feature | Description |
|---------|------------|
| **Inverted index** | Maps words → documents (instant lookup) |
| **Fuzzy matching** | Finds results even with typos |
| **Aggregations** | Group, sum, average search results |
| **Geo queries** | "Find restaurants within 2km" |
| **Auto-complete** | Suggest-as-you-type |
| **Synonyms** | "phone" matches "mobile", "cellphone" |
| **Multi-language** | Analyzers for 40+ languages |
| **Near real-time** | Indexed data searchable within ~1 second |

### When to Use Elasticsearch

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Product search (e-commerce) | Primary transactional database |
| Log analytics (ELK stack) | Financial transactions (no ACID) |
| Full-text search in apps | Data requiring strict consistency |
| Auto-complete / typeahead | Very small datasets |
| "Find nearby" geo queries | Apps that don't need search |

### Companies Using Elasticsearch

| Company | How |
|---------|-----|
| **Wikipedia** | Full-text search across all articles |
| **Flipkart** | Product search ("red running shoes under 2000") |
| **Uber** | Search for places, addresses |
| **Zomato** | Restaurant search ("pizza near me") |
| **Netflix** | Search across movie titles, descriptions |
| **GitHub** | Code search across repositories |

---

<a id="mongodb"></a>
## MongoDB — Document Database

### What It Is (Analogy)

MongoDB is like a **binder where each page can have a different structure**. One page might have name + email. The next page has name + email + phone + address + preferences. No rigid schema — you add fields whenever you want.

### Data Model

```
MongoDB stores BSON (Binary JSON) documents:

Collection: users
  ┌──────────────────────────────────────────────┐
  │ {                                             │
  │   _id: ObjectId("507f1f77bcf86cd799439011"), │
  │   name: "Alice",                              │
  │   email: "alice@email.com",                   │
  │   age: 30,                                    │
  │   address: {                                  │
  │     street: "123 Main St",                    │
  │     city: "Mumbai",                           │
  │     pincode: "400001"                         │
  │   },                                          │
  │   hobbies: ["reading", "coding", "music"],    │
  │   metadata: {                                 │
  │     signupDate: ISODate("2023-01-15"),        │
  │     lastLogin: ISODate("2024-07-26")          │
  │   }                                           │
  │ }                                             │
  └──────────────────────────────────────────────┘

  (Next document can have completely different fields!)
```

### Key Features

| Feature | Description |
|---------|------------|
| **Flexible schema** | Add/remove fields without migrations |
| **Rich queries** | Query nested fields, arrays, ranges |
| **Aggregation pipeline** | Multi-stage data processing (like SQL GROUP BY on steroids) |
| **Indexing** | Single field, compound, text, geo, TTL indexes |
| **Sharding** | Built-in horizontal scaling |
| **Replica sets** | Auto-failover replica set (primary + secondaries) |
| **GridFS** | Store large files by splitting into chunks |

### When to Use MongoDB

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Rapid prototyping (schema can evolve) | Complex joins across collections |
| Content management (CMS) | Financial transactions (weak ACID) |
| IoT data (flexible device schemas) | Apps requiring strict schema enforcement |
| Mobile apps (sync offline) | Heavy reporting/analytics |

### Companies Using MongoDB

| Company | How |
|---------|-----|
| **Uber** (early) | Trip metadata |
| **eBay** | Product catalog metadata |
| **Adobe** | Content management |
| **BigBasket** | Product catalog, inventory |
| **Shopee** | Product listings |

---

<a id="s3"></a>
## Amazon S3 — Object Storage

### What It Is (Analogy)

S3 is like a **warehouse with infinite shelves**. You put boxes (files) on shelves, each with a unique label (key). When you need a box, you fetch it by label. The warehouse never fills up, never breaks, and costs pennies per box.

### How It Works

```
S3 Bucket Structure:

  Bucket: my-app-assets
    │
    ├── images/
    │   ├── user-1001-avatar.jpg      (key: images/user-1001-avatar.jpg)
    │   ├── user-1002-avatar.jpg
    │   └── product-500-thumb.jpg
    │
    ├── videos/
    │   ├── video-001.mp4             (up to 5 TB per object!)
    │   └── video-002.mp4
    │
    └── backups/
        └── db-backup-2024-07-26.sql

  Durability: 99.999999999% (11 nines)
  → That means if you store 10,000 files, you'd lose one every 10 MILLION years
```

### Storage Classes (Cost Optimization)

```
STANDARD:        Frequently accessed (hot data)
                  → $0.023/GB/month. Immediate access.

STANDARD-IA:     Infrequently accessed (warm data)
                  → $0.0125/GB/month. Immediate access.

GLACIER:         Archive (cold data)
                  → $0.004/GB/month. Retrieval takes 1-5 min.

GLACIER DEEP ARCHIVE: Long-term backup
                  → $0.00099/GB/month. Retrieval takes 12 hours.
```

### Companies Using S3

| Company | How |
|---------|-----|
| **Netflix** | Movie files, thumbnails, artwork |
| **Airbnb** | Property photos, user avatars |
| **Amazon** | Product images, reviews data |
| **Pinterest** | Pin images |
| **Twitter** | Media attachments |

### When to Use S3

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| File/image/video storage | Frequent small random reads (use DB) |
| Backups and archives | Transactional data |
| Static website hosting | Data needing complex queries |
| Data lake (analytics input) | Low-latency serving (use CDN in front) |

---

<a id="spanner"></a>
## Google Spanner — Global ACID Database

### What It Is (Analogy)

Spanner is PostgreSQL **that works across the entire planet** with ACID guarantees. You can write in Mumbai and read in New York and both see the same consistent data. It achieves this using **atomic clocks** in Google data centers.

### How It Works

```
Spanner's Global Architecture:

  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
  │  Mumbai DC     │  │  Virginia DC   │  │  Dublin DC     │
  │  (read+write)  │  │  (read+write)  │  │  (read+write)  │
  │                │  │                │  │                │
  │  Shard A,B     │  │  Shard A,B     │  │  Shard A,B     │
  │  (replicated)  │  │  (replicated)  │  │  (replicated)  │
  │                │  │                │  │                │
  │  ⏰ Atomic Clock│  │  ⏰ Atomic Clock│  │  ⏰ Atomic Clock│
  └────────┬───────┘  └────────┬───────┘  └────────┬───────┘
           │                   │                   │
           └───────────────────┼───────────────────┘
                               │
                    Global Paxos consensus
                    (ensures ACID across regions)
```

### Key Features

| Feature | Description |
|---------|------------|
| **Global ACID** | External consistency across continents |
| **Atomic clocks** | GPS + atomic clocks timestamp every transaction |
| **SQL interface** | Standard SQL queries |
| **Auto-sharding** | Splits data automatically as it grows |
| **99.999% availability** | Survives entire data center failures |

### When to Use Spanner

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Financial systems across regions | Small apps |
| Global inventory management | Apps not on GCP |
| When you need global strong consistency | When eventual consistency is acceptable |
| Ad tech (real-time bidding) | Simple CRUD apps |

### Companies Using Spanner

| Company | How |
|---------|-----|
| **Google** | Google Play, AdWords, internal systems |
| **Google Maps** | Route data, place data |
| **Uber** (some) | Trip data (evaluated/migrated parts) |

---

<a id="clickhouse"></a>
## ClickHouse — Columnar Analytics Database

### What It Is (Analogy)

Traditional databases store data **row by row** (good for reading one user's full profile). ClickHouse stores data **column by column** (good for "what's the average age of all 10 million users?").

```
ROW-ORIENTED (MySQL/PostgreSQL):
  Row 1: [user_id=1, name="Alice", age=30, city="Mumbai"]
  Row 2: [user_id=2, name="Bob",   age=25, city="Delhi"]
  Row 3: [user_id=3, name="Carol", age=35, city="Pune"]

  → To get average age, read ALL columns of ALL rows. Wasteful.

COLUMN-ORIENTED (ClickHouse):
  user_id column: [1, 2, 3]
  name column:    ["Alice", "Bob", "Carol"]
  age column:     [30, 25, 35]
  city column:    ["Mumbai", "Delhi", "Pune"]

  → To get average age, only read the "age" column. 4x faster, 4x less I/O.
```

### Key Features

| Feature | Description |
|---------|------------|
| **100x faster analytics** | Billions of rows in seconds |
| **Compression** | Columnar data compresses 5-10x better than row data |
| **Vectorized execution** | Process batches of values at once (CPU cache friendly) |
| **Real-time ingestion** | Stream data in continuously |
| **SQL interface** | Standard SQL queries |

### When to Use ClickHouse

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Analytics dashboards | Transaction processing (OLTP) |
| Log/event analysis | Apps needing row-level updates |
| Real-time metrics | Point lookups (single row reads) |
| User behavior analytics | Apps needing strong consistency |

### Companies Using ClickHouse

| Company | How |
|---------|-----|
| **Uber** | Geospatial analytics, trip analytics |
| **Cloudflare** | DNS query analytics (billions of rows) |
| **Spotify** | Listening analytics |
| **Twitter** | Real-time analytics |
| **ByteDance (TikTok)** | Event analytics |

---

<a id="snowflake"></a>
## Snowflake — Cloud Data Warehouse

### What It Is (Analogy)

Snowflake is like a **super-powered spreadsheet in the cloud** that can handle petabytes of data. You don't manage any servers — you just write SQL queries and Snowflake figures out the compute.

### Key Difference from ClickHouse

```
ClickHouse:                   Snowflake:
  You manage servers            Fully managed (SaaS)
  Real-time ingestion           Batch-oriented (load then query)
  Faster for real-time          Better for ad-hoc analysis
  Open source                   Commercial (expensive)
  One purpose: fast OLAP        Multi-purpose: warehouse + sharing
```

### Companies Using Snowflake

| Company | How |
|---------|-----|
| **Adobe** | Customer analytics |
| **Capital One** | Financial analytics |
| ** DoorDash** | Order analytics, delivery metrics |
| **StreamElements** | Real-time streamer analytics |

---

<a id="neo4j"></a>
## Neo4j — Graph Database

### What It Is (Analogy)

Imagine mapping everyone you know, who they know, and how they're connected. That's a **graph**. Neo4j stores data as **nodes** (entities) and **edges** (relationships). It's built specifically for connected data.

```
Relational DB: To find friends-of-friends-of-friends:

  SELECT ... FROM users u1
  JOIN friends f1 ON u1.id = f1.user_id
  JOIN users u2 ON f1.friend_id = u2.id
  JOIN friends f2 ON u2.id = f2.user_id
  JOIN users u3 ON f2.friend_id = u3.id
  JOIN friends f3 ON u3.id = f3.user_id
  → 6 JOINs. Each join multiplies work. Gets exponentially slow.

Graph DB: Same query:

  MATCH (a)-[:FRIEND*3]->(friend)
  WHERE a.name = "Alice"
  RETURN friend
  → One elegant query. Traverses graph directly. Milliseconds.
```

### When to Use Neo4j

| ✅ Use For | ❌ Don't Use For |
|-----------|-----------------|
| Social networks (friend graphs) | Simple CRUD apps |
| Recommendation engines | Time-series data |
| Fraud detection (connection analysis) | Document storage |
| Network/infrastructure mapping | High-volume writes |

### Companies Using Neo4j

| Company | How |
|---------|-----|
| **Facebook** (conceptually, uses custom graph) | Social graph |
| **LinkedIn** | Professional network connections |
| **Google** (conceptually, uses custom) | Knowledge Graph |

---

<a id="sqlite"></a>
## SQLite — Embedded Database

### What It Is (Analogy)

SQLite is a database that lives **inside your app** — no server, no installation, no configuration. It's a single file on disk. Think of it as a structured text file that understands SQL.

### Why WhatsApp Uses SQLite

```
WhatsApp stores ALL messages, contacts, and media metadata
on your phone using SQLite.

  Your Phone
  ┌─────────────────────────────┐
  │  WhatsApp App               │
  │  ┌─────────────────────┐    │
  │  │ SQLite Database      │    │
  │  │ (msgstore.db)        │    │
  │  │                      │    │
  │  │ messages table       │    │
  │  │ contacts table       │    │
  │  │ chat_lists table     │    │
  │  │ (single file on disk)│    │
  │  └─────────────────────┘    │
  └─────────────────────────────┘

  No server needed. The app reads/writes directly to the file.
  Fast, reliable, zero configuration.
```

### Key Features

| Feature | Description |
|---------|------------|
| **Zero config** | No server, no setup, no DBA |
| **Single file** | Entire database is one file |
| **ACID compliant** | Full transaction support |
| **Public domain** | Free for any use |
| **Reliable** | Used on billions of devices |
| **Small footprint** | ~400KB library size |

### Companies Using SQLite

| Company | How |
|---------|-----|
| **WhatsApp** | All message storage on client devices |
| **Apple** | Core data storage on iOS/macOS |
| **Google** | Chrome history, cookies |
| **Android** | App data storage |
| **Firefox** | Bookmarks, history |

---

<a id="comparison-table"></a>
## Master Comparison Table

| Database | Type | Best For | Scale | Consistency | Used By |
|----------|------|----------|-------|------------|---------|
| **Redis** | KV / In-memory | Cache, sessions, leaderboards | 1M ops/sec | Eventual (async replication) | Twitter, Instagram, Tinder |
| **MySQL** | Relational | Transactions, e-commerce | Sharded → billions | ACID | Facebook, Flipkart, Twitter |
| **PostgreSQL** | Relational+ | Complex queries, GIS, AI | Single → millions | ACID (MVCC) | Instagram, Uber, Spotify |
| **Cassandra** | Wide-column | Time-series, write-heavy | Petabytes | Tunable (ONE→ALL) | Netflix, Apple, Instagram |
| **DynamoDB** | KV (managed) | Serverless, simple lookups | Trillions of items | Tunable | Amazon, Airbnb, Supercell |
| **Bigtable** | Wide-column | Massive throughput | Petabytes | Eventual | Google, YouTube, Snapchat |
| **Elasticsearch** | Search | Full-text search, analytics | Billions of docs | Eventual | Wikipedia, Flipkart, Uber |
| **MongoDB** | Document | Flexible schema, CMS | Sharded → billions | Eventual (some ACID) | Uber, eBay, Adobe |
| **S3** | Object storage | Files, images, videos | Exabytes | Strong-read-after-write | Netflix, Airbnb, Twitter |
| **Spanner** | Global SQL | Global ACID transactions | Petabytes | External consistency | Google, AdWords |
| **ClickHouse** | Columnar | Analytics, dashboards | Billions of rows | Eventual | Uber, Cloudflare, Spotify |
| **Snowflake** | Data warehouse | BI, reporting, analytics | Petabytes | ACID | Adobe, Capital One |
| **Neo4j** | Graph | Social graphs, recommendations | Billions of nodes | ACID | LinkedIn, NASA |
| **SQLite** | Embedded | On-device storage | GBs | ACID | WhatsApp, Apple, Google |

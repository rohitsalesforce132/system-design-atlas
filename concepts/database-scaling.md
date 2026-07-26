# Database Scaling: Sharding, Replication & Partitioning

## The Problem (Analogy First)

Imagine a filing cabinet with 10 billion files in it. Finding one file takes forever. Even worse, 100 people all trying to open the same cabinet at once creates a bottleneck.

**Solution:** Split the files across 100 cabinets. Now 100 people can search 100 cabinets simultaneously.

That's database scaling — splitting data so multiple machines handle the load in parallel.

```
                    ┌──────────────────────┐
  10,000 queries/sec ──►│  SINGLE DATABASE     │ ◄── CPU at 100%, slow, dying
                    │  (10 billion rows)    │
                    └──────────────────────┘

  Split into 10 shards:

  Query for User #12345 → Route to Shard 3
                    ┌──────────┐
                    │ Shard 0  │ users 0-1M
                    │ Shard 1  │ users 1M-2M
                    │ Shard 2  │ users 2M-3M
                    │ Shard 3  │ users 3M-4M ◄── (User #12345 is here)
                    │ ...      │
                    │ Shard 9  │ users 9M-10M
                    └──────────┘
  Each shard handles only 1,000 queries/sec. Easy.
```

## Scaling Approaches Overview

There are two fundamental directions:

```
Vertical Scaling (Scale UP)          Horizontal Scaling (Scale OUT)
┌────────────────────────┐           ┌──────┐ ┌──────┐ ┌──────┐
│  Bigger machine         │           │Machine│ │Machine│ │Machine│
│  More RAM (1TB)         │           │  1    │ │  2    │ │  3    │
│  More CPU (64 cores)    │           │      │ │      │ │      │
│  Faster SSD             │           │ DB A  │ │ DB B  │ │ DB C  │
│                         │           │      │ │      │ │      │
│  $$$$$$ expensive       │           │ Cheap│ │ Cheap│ │ Cheap│
│  Hard limit (max hardware)│          └──────┘ └──────┘ └──────┘
└────────────────────────┘           Linear scaling, no hard limit
```

- **Vertical:** Buy a bigger server. Simple, but has a ceiling.
- **Horizontal:** Add more servers. Complex, but infinite scale.

Within horizontal scaling, the main techniques are **Replication** and **Sharding**.

## Replication (Copying Data)

### Master-Slave (Primary-Replica) Replication

```
                    ┌──────────────┐
  WRITE ──────────►│  MASTER DB    │
                    │  (all writes) │
                    └──────┬───────┘
                           │ (async replication)
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Replica1│   │Replica2│   │Replica3│
         │(reads) │   │(reads) │   │(reads) │
         └────────┘   └────────┘   └────────┘
```

- **Master** handles all writes (INSERT, UPDATE, DELETE).
- **Replicas** handle reads (SELECT).
- Why? Most apps are read-heavy (80-95% reads). Replicas spread read load.

**Example:**
- 1 Master handles 10K writes/sec.
- 5 Replicas handle 50K reads/sec total.
- **Total capacity:** 10K writes + 50K reads.

**Problem:** If master dies, one replica is promoted to master (automatic failover).

### Master-Master (Multi-Master) Replication

```
         ┌────────┐  ←──replicate──►  ┌────────┐
WRITE ──►│Master A│  ──replicate──►  │Master B│◄── WRITE
         │(Mumbai)│                   │(US East)│
         └────────┘                   └────────┘
```

- Both masters accept writes.
- **Pros:** Local writes in each region (Mumbai users write to Master A, US users write to Master B).
- **Cons:** Conflict resolution (what if user updates profile in both regions simultaneously?). Very complex.

### Synchronous vs Asynchronous Replication

| Type | How it works | Latency | Use case |
|------|-------------|---------|----------|
| **Synchronous** | Master waits for replica to confirm before returning success | Higher write latency | Financial data (must not lose) |
| **Asynchronous** | Master returns success immediately, replica catches up later | Low write latency | Most apps (accept slight lag) |

## Sharding (Splitting Data)

Sharding splits a large table across multiple databases. Each shard contains a subset of data.

### Sharding Strategies

#### 1. Range-Based Sharding
```
Shard 0: users with ID 0 - 1,000,000
Shard 1: users with ID 1,000,001 - 2,000,000
Shard 2: users with ID 2,000,001 - 3,000,000
```

**Pros:** Easy to implement. Easy to add new shards.
**Cons:** Hotspots! If IDs are auto-increment, all new users go to the last shard.

#### 2. Hash-Based Sharding
```
shard = hash(user_id) % num_shards

hash(12345) % 10 = 3  → Shard 3
hash(12346) % 10 = 7  → Shard 7
hash(12347) % 10 = 0  → Shard 0
```

**Pros:** Even distribution. No hotspots.
**Cons:** Adding a new shard requires rehashing ALL data (massive migration). Fix: consistent hashing.

#### 3. Directory-Based Sharding
```
Lookup table:
user_id → shard_number
12345   → Shard 3
67890   → Shard 1

Query flow: Check lookup → route to correct shard
```

**Pros:** Flexible. Can move data between shards easily.
**Cons:** Lookup table is a bottleneck and single point of failure.

#### 4. Geographic Sharding
```
Shard India: Indian users (Mumbai data center)
Shard US: American users (Virginia data center)
Shard EU: European users (Dublin data center)
```

**Pros:** Low latency (data physically near user). Data sovereignty compliance (GDPR).
**Sharding key:** user's country/region.

### Consistent Hashing (The Smart Way)

```
Imagine a ring of hash values (0 to 2^32 - 1):

         0
       /    \
   Shard A   Shard B
      |        |
      |        |
   Shard D   Shard C
       \    /
       2^32

Each shard owns a range on the ring.
When adding Shard E, it only takes data from its neighbors — not all shards.
```

**Why it matters:** Adding/removing shards only moves data from adjacent nodes, not the entire dataset. This is how Cassandra, DynamoDB, and Redis Cluster work.

## What Shard Key to Choose?

The shard key determines which shard stores a row. **This is the most important decision.**

| Good Shard Key | Bad Shard Key |
|---------------|--------------|
| `user_id` (high cardinality) | Country (low cardinality — only ~200 countries) |
| `timestamp` (with care) | Timestamp (can create hot shard for "today") |
| `user_id + timestamp` | Boolean flag (only 2 values) |

**Rules:**
1. **High cardinality:** Many possible values → even distribution.
2. **Avoid hot spots:** Don't pick a key where most traffic goes to one value.
3. **Query locally:** Pick a key that matches your queries. If you always query "user's messages," shard by `user_id` so all of a user's messages are on the same shard.

## Common Problems with Sharding

### Problem 1: Cross-Shard Joins
```
-- This works on single DB:
SELECT * FROM orders o JOIN users u ON o.user_id = u.id;

-- This FAILS if orders and users are on different shards:
-- You'd need to query both shards and join in application code.
```

**Fix:** Careful shard key selection. Keep related data on the same shard.

### Problem 2: Distributed Transactions
```
User transfers money from Account A (Shard 1) to Account B (Shard 2).
Need: ACID transaction across two shards.
```

**Fix:** Use distributed transaction protocols (2PC — Two-Phase Commit) or Saga pattern.

### Problem 3: Rebalancing
```
Shard 3 has 10x more data than others (hot user).
Need to split Shard 3 into 3a and 3b.
```

**Fix:** Consistent hashing minimizes data movement. Or use virtual nodes.

## When to Shard vs Not to Shard

```
Should I shard?

  Users < 1 million? ──NO──► Single DB with read replicas
  │
  Users 1-10 million? ──MAYBE──► Vertical scaling + read replicas + caching
  │
  Users 10M+? ──YES──► Shard by user_id
  │
  Petabyte-scale? ──YES──► Sharding + multi-region replication
```

**Sharding is complex. Delay it as long as possible.** Cache first, optimize queries second, add read replicas third, THEN shard.

## SQL vs NoSQL for Scaling

| Aspect | SQL (PostgreSQL, MySQL) | NoSQL (Cassandra, MongoDB, DynamoDB) |
|--------|------------------------|--------------------------------------|
| **Scale** | Vertical first, horizontal is hard | Built for horizontal scaling |
| **Schema** | Rigid (must define columns) | Flexible (add fields anytime) |
| **Joins** | Supported | Not supported (denormalize) |
| **ACID** | Yes | Eventual consistency (usually) |
| **Best for** | Complex relationships, transactions | Massive scale, simple queries |

**The big apps mostly use both:**
- **Facebook:** MySQL (sharded) + RocksDB + custom graph store
- **Twitter:** MySQL (sharded) + Redis (timelines)
- **Netflix:** Cassandra (viewing history) + PostgreSQL (billing)
- **Uber:** PostgreSQL (transactions) + SchemaRDD (geospatial) + Cassandra
- **Amazon:** DynamoDB (shopping cart) + Amazon Aurora (transactions)

## Real-World Example: Facebook's MySQL Sharding

Facebook has **4.5 billion users**. Here's how they shard:

```
Shard Key: user_id
Number of shards: thousands
Sharding strategy: range-based on user_id ranges

Each shard:
  - MySQL instance with ~1-5 million users
  - One master + multiple read replicas
  - Each user's data (posts, comments, photos metadata) stays together

Cross-shard queries:
  - News Feed: Fanout-on-write (pre-compute feeds, store in memcached)
  - Search: Separate Elasticsearch index
  - Notifications: Separate system
```

## How YOU Can Build This

### Level 1: Single Database + Read Replica
```
App ──writes──► PostgreSQL Master
  └──reads───► PostgreSQL Replica (for analytics, reports)
```

### Level 2: Vertical Scale + Caching
```
App ──► Redis Cache ──► PostgreSQL Master
```

### Level 3: Sharding with Citus (PostgreSQL extension)
```
App ──► Citus Coordinator
         ├──► Worker Node 1 (users hash 0-33%)
         ├──► Worker Node 2 (users hash 34-66%)
         └──► Worker...
```

### Level 4: DynamoDB / Cassandra (Sharding Built-In)
```
App ──► DynamoDB (auto-shards behind the scenes)
  Partition key: user_id
  DynamoDB handles all sharding, replication, rebalancing automatically.
```

## Common Interview Questions

**Q: How do you handle a billion users in a single database?**
A: You don't. You shard by user_id. Each shard holds a subset of users. Use consistent hashing to minimize data movement when adding shards. Combine with read replicas per shard for read scaling.

**Q: What happens if a shard dies?**
A: Each shard should have replicas. If a shard master dies → promote replica automatically. If all replicas of a shard die → data loss for that shard's data unless you have backups.

** database?**
A: They don't. They split across thousands of shards. Each shard is a commodity server. They use massive redundancy (3x replication minimum).

**Q: How do you shard a messaging app?**
A: Shard by `conversation_id`. All messages in a conversation land on the same shard. This lets you fetch a chat history with a single-shard query. If you shard by `user_id`, messages from two users in a conversation would be on different shards — making chat history queries require cross-shard joins.

**Q: What's the difference between partitioning and sharding?**
A: **Partitioning** splits tables within a single database (e.g., partition a table by month). **Sharding** splits tables across multiple machines. Partitioning is for a single machine's query performance. Sharding is for distributing load across machines.

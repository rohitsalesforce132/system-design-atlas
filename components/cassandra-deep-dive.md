# Cassandra — The Complete Deep Dive

> Netflix stores viewing history for 260M users on Cassandra. Apple runs 100,000+ Cassandra nodes. This guide covers how its peer-to-peer architecture, LSM-tree storage, and gossip protocol work inside.

---

## Table of Contents

1. [What Problem Cassandra Solves](#the-problem)
2. [Peer-to-Peer Architecture (No Master)](#architecture)
3. [Gossip Protocol — How Nodes Discover Each Other](#gossip)
4. [Data Model Deep Dive](#data-model)
5. [Write Path — How a Write Works Internally](#write-path)
6. [Read Path — Memtable, SSTable, Bloom Filter](#read-path)
7. [Compaction Strategies](#compaction)
8. [Tombstones — How Deletes Work](#tombstones)
9. [Tunable Consistency — The Quorum Math](#consistency)
10. [Replication and Topology](#replication)
11. [Repair Mechanisms](#repair)
12. [Anti-Patterns — What NOT to Do](#antipatterns)
13. [How Real Companies Use Cassandra](#real-apps)
14. [How YOU Can Build This](#build)

---

<a id="the-problem"></a>
## What Problem Cassandra Solves

### The Write-Throughput Problem

```
PostgreSQL (excellent for reads, ACID, complex queries):
  Write path: UPDATE → Read row → Lock row → Modify → Write to WAL → Update B-tree index → Commit
  → Every write involves: read-modify-write + index update + locking
  → Max throughput: ~5,000-20,000 writes/sec per node
  → At 100,000+ writes/sec, PostgreSQL needs many shards

Cassandra (built for writes):
  Write path: Append to commit log → Write to memtable (in-memory) → Done
  → No locks, no read-before-write, no index update
  → Just SEQUENTIAL APPEND to disk + in-memory update
  → Throughput: 100,000+ writes/sec per node
  → Linear scaling: 10 nodes = 1M writes/sec

  Cassandra trades query flexibility (no JOINs, no complex queries)
  for MASSIVE write throughput and ZERO downtime.
```

### What Cassandra Optimizes For

```
1. WRITE THROUGHPUT:    100K+ writes/sec per node, linear scaling
2. AVAILABILITY:        No master = no single point of failure
3. LINEAR SCALABILITY:  Add nodes → proportional capacity increase
4. MULTI-DATACENTER:    Built-in cross-DC replication
5. EVENTUAL CONSISTENCY: Tunable — from ONE to ALL

What it gives up:
  - No JOINs
  - No complex ad-hoc queries
  - No ACID transactions (atomic within a partition only)
  - Eventual consistency (configurable, but not immediate by default)
```

---

<a id="architecture"></a>
## Peer-to-Peer Architecture (No Master)

### The Key Difference from Most Databases

```
MOST DATABASES (Master-Slave):
  ┌──────────┐
  │  MASTER   │  ← All writes go here (single point of coordination)
  └────┬─────┘
       │ (replicate)
  ┌────▼─────┐  ┌──────────┐
  │ REPLICA 1│  │ REPLICA 2│  ← Read-only, just copies
  └──────────┘  └──────────┘

  Problem: If master dies → election → downtime
  Problem: Master is a bottleneck for writes

CASSANDRA (Peer-to-Peer):
  ┌──────────┐     ┌──────────┐
  │  NODE 1   │◄──►│  NODE 2   │
  └────┬─────┘     └────┬─────┘
       │                │
  ┌────┴─────┐     ┌────┴─────┐
  │  NODE 4   │◄──►│  NODE 3   │
  └──────────┘     └──────────┘

  ALL nodes are equal.
  ANY node can accept reads AND writes.
  No master, no election, no failover time.
  If any node dies → others handle its data (replicas).
```

### How a Request Reaches the Right Node

```
Client sends write to ANY node (e.g., Node 2):

  Client ──write──► Node 2 (the "coordinator")

  Node 2 calculates: Which nodes own this data?
    partition = hash(partition_key) % token_range
    → Determines which nodes are replicas for this data

  Node 2 forwards the write to the replica nodes:
    Node 2 ──write──► Node 1 (replica 1)
    Node 2 ──write──► Node 3 (replica 2)
    Node 2 ──write──► Node 4 (replica 3)

  Based on consistency level:
    ONE:      Wait for 1 replica to acknowledge → respond to client
    QUORUM:   Wait for majority (2 of 3) → respond
    ALL:      Wait for all 3 → respond

  Node 2 (coordinator) is NOT special — any node can be coordinator.
  The client picks a random node (or uses a load-balancing policy).
```

---

<a id="gossip"></a>
## Gossip Protocol — How Nodes Discover Each Other

### The Problem

With no master, how do nodes know:
1. Which nodes are alive?
2. Which nodes own which data?
3. When new nodes join or leave?

### Gossip (Epidemic Protocol)

```
Every 1 second, each node picks 1-3 random nodes and exchanges state:

  Node 1 knows: {Node1: UP, Node2: UP, Node3: UP, Node4: DOWN}

  Node 1 gossips with Node 2:
    "I know: Node1=UP, Node2=UP, Node3=UP, Node4=DOWN"

  Node 2 responds:
    "I know: Node1=UP, Node2=UP, Node3=UP, Node4=UP (just recovered!)"

  Both merge their knowledge:
    Node 1 now knows: Node4=UP (learned from Node 2)

  Like a rumor spreading through a crowd:
    After log(N) rounds, all nodes know about all other nodes.
    For 100 nodes: ~7 rounds = ~7 seconds for info to propagate.
```

### What Gossip Communicates

```
Gossip message contains:
{
  "node_id": "node-1",
  "status": "UP",
  "heartbeat": 1234567,
  "tokens": [0, 257, 514, ...],      // which data ranges this node owns
  "datacenter": "DC1",
  "rack": "RACK-A",
  "schema_version": "abc123",
  "load": 0.75
}

Heartbeat: Incremented every second. If heartbeat doesn't advance
for a while (default 10s), node is suspected dead.

Detecting a dead node:
  1. Node 2 notices Node 4's heartbeat hasn't advanced in 5s → SUSPECT
  2. Gossips suspicion to others
  3. After 10s with no heartbeat → CONFIRM DOWN
  4. Other nodes start handling Node 4's data (via replicas)
```

---

<a id="data-model"></a>
## Data Model Deep Dive

### Keyspace → Table → Partition → Row → Column

```
KEYSPACE (like a database):
  CREATE KEYSPACE my_app
    WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 3};

TABLE (like a SQL table, but called a "column family"):
  CREATE TABLE users (
    user_id    uuid,        ← PARTITION KEY
    created_at timestamp,   ← CLUSTERING KEY
    name       text,
    email      text,
    age        int,
    PRIMARY KEY ((user_id), created_at)
  );

  The PRIMARY KEY has two parts:
    PARTITION KEY: (user_id)   → Determines which node stores the data
    CLUSTERING KEY: created_at → Orders rows WITHIN the partition
```

### Partition Key — The Most Important Decision

```
Data distribution:
  hash(user_id) → token → node that owns this token range

  user_id=1001 → hash → token 3500 → Node 2 (owns tokens 3000-6000)
  user_id=1002 → hash → token 7200 → Node 3 (owns tokens 6000-9000)

  All data for user_id=1001 is on the SAME partition → SAME set of nodes.
  This means:
    → Reading all data for one user = single partition read (fast)
    → Writing data for one user = single partition write (fast)
    → You CANNOT query across partitions efficiently (no JOINs)

  RULE: Design partition key around your query patterns.
    Query: "Get all messages for user X"
    → Partition key: user_id
    → All messages for user X are in one partition, sorted by timestamp
```

### Clustering Key — Ordering Within a Partition

```
  PRIMARY KEY ((user_id), created_at DESC)

  Partition for user_id=1001:
  ┌─────────────────────────────────────────────────────┐
  │  created_at          │ name    │ message             │
  │──────────────────────┼─────────┼─────────────────────│
  │  2024-07-26 10:00    │ Alice   │ "Hello"             │
  │  2024-07-26 09:55    │ Bob     │ "Hi there"          │
  │  2024-07-26 09:50    │ Carol   │ "Good morning"      │
  └─────────────────────────────────────────────────────┘

  Rows are SORTED by created_at within the partition.
  → "Get last 10 messages for user 1001" = sequential read (very fast)
  → No global sort needed — data is pre-sorted on disk

  Query:
    SELECT * FROM messages
    WHERE user_id = 1001
    AND created_at > '2024-07-26 09:00'
    ORDER BY created_at DESC
    LIMIT 10;
    → Single partition read. Milliseconds.
```

### Static Columns

```
  CREATE TABLE user_purchases (
    user_id    uuid,        ← Partition key
    purchase_id uuid,       ← Clustering key
    name       text STATIC,  ← Same for ALL rows in this partition
    email      text STATIC,
    total_spent counter STATIC,
    PRIMARY KEY ((user_id), purchase_id)
  );

  Static columns are stored ONCE per partition (not per row).
  → user's name and email stored once, shared by all their purchases.
  → Saves space + update once = visible for all rows.
```

---

<a id="write-path"></a>
## Write Path — How a Write Works Internally

This is where Cassandra's speed comes from.

```
Client: INSERT INTO users (user_id, name, email) VALUES (1001, 'Alice', 'a@b.com')

Step 1: Write to Commit Log (sequential append to disk)
  ┌──────────────────────────────┐
  │  COMMIT LOG (disk)            │
  │  [previous entries...]        │
  │  [NEW: user_id=1001, name=...]│ ← Appended to end of file
  └──────────────────────────────┘

  → SEQUENTIAL I/O (fastest possible disk write)
  → Purpose: Crash recovery (if node dies before flushing memtable)

Step 2: Write to Memtable (in-memory sorted map)
  ┌──────────────────────────────┐
  │  MEMTABLE (RAM)               │
  │  ┌─────────────────────────┐ │
  │  │ key=1001:               │ │
  │  │   name = "Alice"        │ │ ← Update in-memory
  │  │   email = "a@b.com"     │ │
  │  │ key=1002:               │ │
  │  │   name = "Bob"          │ │
  │  └─────────────────────────┘ │
  └──────────────────────────────┘

  → Sorted by partition key
  → In-memory → instant update

Step 3: Acknowledge to Client
  → Client receives "OK" after memtable + commitlog write
  → Total write latency: ~0.1-1ms

Step 4: Memtable Flush (when full — default 128MB or after flush_period)
  ┌──────────────────────────────┐
  │  SSTABLE (on disk)            │
  │  ┌─────────────────────────┐ │
  │  │ Partition 1001:          │ │
  │  │   name = "Alice"         │ │ ← Memtable flushed to disk
  │  │   email = "a@b.com"      │ │   as an immutable SSTable file
  │  │ Partition 1002:          │ │
  │  │   name = "Bob"           │ │
  │  └─────────────────────────┘ │
  └──────────────────────────────┘
  → Memtable is cleared
  → Commit log segment is deleted (data is now in SSTable)
```

### Why This Is So Fast

```
Traditional DB (PostgreSQL B-tree):
  1. Read the B-tree page from disk (if not cached)
  2. Lock the row
  3. Update the row in place
  4. Update the B-tree index
  5. Write to WAL
  6. Commit
  → Multiple disk I/Os (random access)
  → Locking overhead

Cassandra (LSM-tree):
  1. Append to commit log (sequential I/O — fast)
  2. Update memtable (in-memory — instant)
  3. Done
  → ONE sequential disk write + one in-memory update
  → No locks, no read-before-write, no random I/O
  → This is why Cassandra does 100K+ writes/sec
```

---

<a id="read-path"></a>
## Read Path — Memtable, SSTable, Bloom Filter

Reads are more complex because data might be in multiple places.

```
Client: SELECT * FROM users WHERE user_id = 1001

Step 1: Check Memtable (RAM)
  → If data is in memtable (recently written): Return immediately.
  → If not: proceed to SSTables.

Step 2: Check Bloom Filter (for each SSTable)
  ┌───────────────────────────────────────────────────────┐
  │  BLOOM FILTER                                         │
  │                                                       │
  │  A probabilistic data structure that answers:         │
  │    "Might this SSTable contain key 1001?"             │
  │                                                       │
  │  Possible answers:                                    │
  │    "Definitely NOT" → Skip this SSTable (fast)        │
  │    "Possibly yes"   → Check the SSTable (might be     │
  │                       a false positive)               │
  │                                                       │
  │  Bloom filter NEVER has false negatives.              │
  │  It might have false positives (rare, ~1%).           │
  │  → If it says "not here", skip with 100% confidence.  │
  └───────────────────────────────────────────────────────┘

Step 3: Check Partition Summary (sparse index)
  ┌──────────────────────────────────────────────────┐
  │  PARTITION SUMMARY (in memory, ~1% of SSTable)   │
  │                                                  │
  │  Token 1000 → Offset 4096                        │
  │  Token 2000 → Offset 8192                        │
  │  Token 3000 → Offset 12288                       │
  │  ...                                             │
  │  → Binary search: Token for 1001 is between      │
  │    1000 and 2000 → go to offset ~4096-8192       │
  └──────────────────────────────────────────────────┘

Step 4: Check Partition Index (exact offset)
  ┌──────────────────────────────────────────────────┐
  │  PARTITION INDEX (on disk)                        │
  │  Token 1001 → Offset 4567  (exact location)      │
  └──────────────────────────────────────────────────┘

Step 5: Read Data from SSTable
  ┌──────────────────────────────────────────────────┐
  │  SSTABLE DATA (on disk)                           │
  │  Offset 4567:                                     │
  │  Partition 1001:                                  │
  │    name = "Alice", email = "a@b.com"             │
  └──────────────────────────────────────────────────┘

Step 6: Merge Results
  → If multiple SSTables have data for key 1001:
    → Merge by timestamp (latest wins)
    → This is called "read resolution"
```

### Read Performance Characteristics

```
Best case:
  Data in memtable → 0.1ms (RAM read)

Typical case:
  Data in 1 SSTable → 2-5ms (bloom filter + index + data read)

Worst case:
  Data spread across 10 SSTables → 10-30ms
  → Each SSTable check = bloom filter + index + data
  → Compaction helps by reducing number of SSTables
```

---

<a id="compaction"></a>
## Compaction Strategies

### Why Compaction Is Needed

```
Over time, many SSTables accumulate:

  SSTable 1: [key=1001: name="Alice v1"]
  SSTable 2: [key=1001: name="Alice v2"]  ← updated name
  SSTable 3: [key=1001: name="Alice v3"]  ← updated again

  A read for key 1001 must check ALL 3 SSTables.
  → Slow and wasteful.

Compaction:
  Merge SSTables → keep only the LATEST value per key:

  SSTable NEW: [key=1001: name="Alice v3"]  ← only latest
  Old SSTables 1, 2, 3 are deleted.
  → Read checks only 1 SSTable. Fast.
```

### Strategy 1: Size-Tiered Compaction (STCS)

```
How it works:
  Tier 0: [4 SSTables of ~50MB each]
    When 4 similar-sized SSTables accumulate → merge them:
  Tier 1: [1 SSTable of ~200MB]
    When 4 of these accumulate → merge:
  Tier 2: [1 SSTable of ~800MB]
  ...

PROS:
  + Good for write-heavy workloads
  + Low write amplification (each write is compacted few times)
  + Simple to understand

CONS:
  - Reads may check many SSTables (before compaction triggers)
  - Space amplification: temporarily 2x disk space during compaction

BEST FOR: Time-series data, event logs, write-heavy workloads
```

### Strategy 2: Leveled Compaction (LCS)

```
How it works:
  Level 0: [multiple SSTables] → flushed from memtable
  Level 1: [exactly 10 SSTables, each ~100MB, non-overlapping keys]
  Level 2: [exactly 100 SSTables, each ~100MB, non-overlapping keys]
  ...

  Each level is 10x bigger than the previous.
  Keys in Level N+1 don't overlap with Level N.

  When Level 0 is full → merge into Level 1.
  When Level 1 is full → merge into Level 2.

PROS:
  + Excellent read performance (max 1 SSTable per level to check)
  + Bounded space amplification

CONS:
  - High write amplification (data compacted many times as it moves levels)
  - More CPU and I/O for compaction

BEST FOR: Read-heavy workloads, when read latency must be predictable
```

### Strategy 3: Time-Window Compaction (TWCS)

```
How it works:
  Group SSTables by time window (e.g., 1 hour):
  Window 10:00-11:00 → [SSTable A, SSTable B, SSTable C]
  Window 11:00-12:00 → [SSTable D, SSTable E]

  Within each window: size-tiered compaction.
  Across windows: NO compaction (each window is independent).

PROS:
  + Perfect for time-series data (each hour/day is self-contained)
  + Predictable compaction behavior
  + No mixing of old and new data

BEST FOR: Time-series data, logs, metrics (exactly what you'd store in Cassandra)
```

---

<a id="tombstones"></a>
## Tombstones — How Deletes Work

### The Problem with Deletes in an LSM-Tree

```
SSTables are IMMUTABLE (never modified).
You can't just go in and delete a row.

Solution: Write a TOMBSTONE (a marker saying "this data is deleted").

  DELETE FROM users WHERE user_id = 1001;

  → Does NOT remove data from existing SSTables
  → Writes a new entry: {key: 1001, value: TOMBSTONE, timestamp: now}
  → Tombstone goes into memtable → SSTable

  On read:
    → Find all entries for key 1001
    → If latest entry is a tombstone → return "not found"
    → The actual data is still in old SSTables (taking up space)
```

### Tombstone Problems

```
Problem 1: READ LATENCY
  If there are many tombstones, reads must check all of them:
  SSTable 1: [key=1001: data]
  SSTable 2: [key=1001: TOMBSTONE]
  SSTable 3: [key=1001: TOMBSTONE]  ← re-deleted?
  → Read checks all 3, finds latest is tombstone, returns nothing.
  → Wasted I/O checking dead data.

Problem 2: DISK SPACE
  Tombstones occupy disk space until garbage collection.

Problem 3: RESURRECTION
  If tombstone expires (gc_grace_seconds) before all replicas have it:
  → Node A has tombstone (expired) → removes it during compaction
  → Node B was down during delete → still has original data
  → Node B repairs → sends old data back to Node A
  → DELETED DATA COMES BACK! (resurrection)
```

### gc_grace_seconds

```
  Default: 10 days

  Tombstones are kept for gc_grace_seconds (10 days).
  This gives all replicas time to receive the tombstone.

  After gc_grace_seconds:
    → Tombstone is eligible for removal during compaction
    → Assumes all replicas have seen it by now

  If a replica is down for MORE than gc_grace_seconds:
    → It missed the tombstone
    → When it comes back, repair is needed BEFORE compaction removes tombstones
    → Run "nodetool repair" to ensure consistency
```

---

<a id="consistency"></a>
## Tunable Consistency — The Quorum Math

### Consistency Levels

```
WRITE: "How many replicas must confirm before I acknowledge?"

  ANY:      At least 1 replica (can be a hinted handoff)
            → Fastest, weakest. Data could be lost if that node dies.

  ONE:      At least 1 replica acknowledges the write
            → Fast. Data survives if that replica doesn't die.

  TWO:      At least 2 replicas
  THREE:    At least 3 replicas

  QUORUM:   Majority of replicas (ceil(RF/2) + 1)
            → RF=3: need 2, RF=5: need 3

  LOCAL_QUORUM: Quorum in the local datacenter
            → Good for multi-DC: low latency + decent consistency

  EACH_QUORUM: Quorum in EACH datacenter
            → Strongest multi-DC consistency

  ALL:      ALL replicas must acknowledge
            → Strongest, slowest. Any down node blocks writes.

READ: "How many replicas must respond?"

  ONE:      Read from 1 replica (might be stale)
  QUORUM:   Read from majority (with write quorum → strong consistency)
  ALL:      Read from all replicas (slowest)
```

### The Quorum Math Proof

```
CLAIM: QUORUM writes + QUORUM reads = strong consistency

PROOF:
  Replication Factor (RF) = 3
  Quorum (Q) = ceil(RF / 2) + 1 = ceil(1.5) + 1 = 2

  Write at QUORUM: At least Q=2 of 3 replicas have the latest value.
  Read at QUORUM:  At least Q=2 of 3 replicas respond.

  By the PIGEONHOLE PRINCIPLE:
    Total positions in the replica set: 3
    Write group: ≥2 nodes
    Read group: ≥2 nodes
    2 + 2 = 4 > 3

    Therefore, at least 4 - 3 = 1 node must be in BOTH groups.
    That overlap node has the latest write.
    The coordinator compares timestamps and returns the latest value.

  FORMULA for strong consistency:
    write_nodes + read_nodes > replication_factor
    R + W > RF

  For RF=3, Q=2: 2+2=4 > 3 ✓ (strong consistency)
  For RF=3, ONE+ONE: 1+1=2 < 3 ✗ (might miss latest write)
  For RF=5, Q=3: 3+3=6 > 5 ✓ (strong consistency)
```

---

<a id="replication"></a>
## Replication and Topology

### Replication Factor

```
  CREATE KEYSPACE my_app WITH replication = {
    'class': 'NetworkTopologyStrategy',
    'datacenter1': '3',    ← 3 copies in DC1
    'datacenter2': '3'     ← 3 copies in DC2
  };

  Total copies of data: 6 (3 per datacenter)

  RF=1: No redundancy. Node failure = data loss.
  RF=2: Can survive 1 node failure (for reads)
  RF=3: Industry standard. Survives 1 failure with strong consistency.
```

### NetworkTopologyStrategy

```
Cassandra is datacenter/rack-aware:

  Datacenter 1                    Datacenter 2
  ┌─────────────────────┐         ┌─────────────────────┐
  │ Rack A    Rack B    │         │ Rack A    Rack B    │
  │ Node1     Node3     │         │ Node5     Node7     │
  │ Node2     Node4     │         │ Node6     Node8     │
  └─────────────────────┘         └─────────────────────┘

  NetworkTopologyStrategy places replicas:
    → First replica: on the node determined by token ring
    → Second replica: different rack in same DC
    → Third replica: different rack in same DC (if possible)

  This ensures:
    → Rack failure (power outage in Rack A) → replicas in Rack B survive
    → DC failure (entire DC1 down) → DC2 has full copy
```

---

<a id="repair"></a>
## Repair Mechanisms

Cassandra is eventually consistent. Replicas can diverge. Repair brings them back in sync.

### 1. Read Repair (Automatic)

```
When a coordinator reads from multiple replicas:

  Replica A: key=1001, value="Alice v3", ts=100
  Replica B: key=1001, value="Alice v2", ts=90   ← STALE
  Replica C: key=1001, value="Alice v3", ts=100

  Coordinator detects: Replica B is behind
  → Sends latest value ("Alice v3") to Replica B
  → Replica B updates

  This is called READ REPAIR.
  → Happens automatically on every read at QUORUM+
  → Fixes inconsistencies as they're discovered
```

### 2. Anti-Entropy Repair (Manual/Scheduled)

```
  nodetool repair

  → Compares ALL data between replicas
  → Finds and fixes any differences
  → Very I/O intensive → run during low-traffic periods
  → Typically scheduled weekly

  Use case: After a node outage, to catch up a newly returned node
```

### 3. Hinted Handoff

```
  Node 3 is temporarily DOWN.
  Coordinator has a write for Node 3.
  → Coordinator stores a "hint" locally
  → Hint: "When Node 3 comes back, send it this write"
  → When Node 3 recovers → coordinator delivers the hint
  → Node 3 catches up

  If Node 3 is down > max_hint_window (default 3 hours):
    → Hints are discarded
    → Must use anti-entropy repair instead
```

---

<a id="antipatterns"></a>
## Anti-Patterns — What NOT to Do

### 1. Secondary Indexes

```
  CREATE INDEX ON users(email);

Problem:
  → Secondary index is per-node (not global)
  → Query: SELECT * FROM users WHERE email = 'x@y.com'
  → Coordinator must ask EVERY node to check its local index
  → Scatter-gather → very slow in large clusters
  → Unpredictable latency

Fix: Use a separate table designed for the query:
  CREATE TABLE users_by_email (email text PRIMARY KEY, user_id uuid);
  → Lookup user_id by email in one partition read.
```

### 2. Large Partitions

```
  Partition key: city = "Mumbai" (10 million users)
  → All 10M users in ONE partition on 3 nodes
  → Hot partition: 3 nodes overloaded, rest idle

Fix: Use a compound key that distributes evenly:
  PRIMARY KEY ((city, bucket), user_id)
  → bucket = hash(user_id) % 100
  → Distributes Mumbai's 10M users across 100 partitions
```

### 3. JOINs and Cross-Partition Queries

```
  SELECT * FROM orders o JOIN users u ON o.user_id = u.id

  Cassandra DOES NOT SUPPORT JOINS.
  → You must denormalize: store user data in the orders table.
  → Or maintain a separate lookup table.
```

### 4. Too Many Partitions in a Query

```
  SELECT * FROM messages WHERE user_id IN (1, 2, 3, ..., 1000)

Problem:
  → 1000 separate partition reads (scattered across nodes)
  → Coordinator overloaded
  → High latency

Fix: Query one partition at a time (async) or use a different data model.
```

---

<a id="real-apps"></a>
## How Real Companies Use Cassandra

| Company | Use Case | Scale |
|---------|---------|-------|
| **Netflix** | Viewing history (what you watched, where you paused) | 260M users, 500+ billion rows |
| **Apple** | Largest known Cassandra deployment | 100,000+ nodes, petabytes |
| **Instagram** | User activity feeds, story data | Billions of rows |
| **Spotify** | Listening history, playlist metadata | 600M users |
| **Twitter** | Tweet engagement, timeline data | Billions of rows |
| **Reddit** | Votes, comments, hot ranking | Millions of rows/sec |
| **Discord** | Message storage (moved from Cassandra to ScyllaDB) | Billions of messages |

### Netflix Example

```
Netflix stores every viewing event:
  → User starts movie → record
  → User pauses at 23:45 → record
  → User resumes → record
  → User finishes → record

  Table:
  CREATE TABLE viewing_history (
    user_id     uuid,        ← Partition key
    viewed_at   timestamp,   ← Clustering key
    movie_id    uuid,
    position    int,         ← Where they paused (seconds)
    device      text,
    PRIMARY KEY ((user_id), viewed_at)
  );

  Query: "What did user X watch recently?"
    SELECT * FROM viewing_history
    WHERE user_id = X
    ORDER BY viewed_at DESC
    LIMIT 50;
  → Single partition read. <5ms. For 260M users.
```

---

<a id="build"></a>
## How YOU Can Build This

### Docker Setup

```bash
docker run --name cassandra -p 9042:9042 -d cassandra:4.1

# Connect with cqlsh
docker exec -it cassandra cqlsh
```

### Create Schema and Insert Data

```sql
-- Create keyspace
CREATE KEYSPACE my_app WITH replication = 
  {'class': 'SimpleStrategy', 'replication_factor': '1'};

-- Create table
USE my_app;

CREATE TABLE messages (
  conversation_id uuid,
  sent_at         timestamp,
  sender_id       uuid,
  message_text    text,
  PRIMARY KEY ((conversation_id), sent_at)
) WITH CLUSTERING ORDER BY (sent_at DESC);

-- Insert data
INSERT INTO messages (conversation_id, sent_at, sender_id, message_text)
VALUES (uuid(), toTimestamp(now()), uuid(), 'Hello World!');

-- Query: Get recent messages for a conversation
SELECT * FROM messages
WHERE conversation_id = ?
ORDER BY sent_at DESC
LIMIT 20;
```

---

## Common Interview Questions

**Q: Why is Cassandra optimized for writes?**

A: Its LSM-tree (Log-Structured Merge-tree) storage engine. Writes are sequential appends to a commit log + in-memory memtable update. No random disk I/O, no locks, no read-before-write, no B-tree index updates. This makes writes O(1) per operation. The cost is shifted to reads, which may need to check multiple SSTables (mitigated by bloom filters and compaction).

**Q: Explain the gossip protocol.**

A: Every second, each Cassandra node picks 1-3 random peers and exchanges cluster state (who's alive, heartbeat counters, token ownership, schema version). Like a rumor spreading through a crowd, information propagates in O(log N) rounds. This allows the cluster to operate without a central coordinator — all nodes eventually learn about membership changes, node failures, and token range shifts.

**Q: How do deletes work in Cassandra?**

A: Cassandra doesn't delete data immediately — SSTables are immutable. Instead, it writes a tombstone (a marker saying "this data is deleted at timestamp T"). On read, the coordinator merges all versions by timestamp — if the latest is a tombstone, it returns "not found." Tombstones are kept for gc_grace_seconds (default 10 days) to ensure all replicas have received them, then removed during compaction. If a replica was down longer than gc_grace_seconds, you must run repair before compaction, or deleted data can "resurrect."

**Q: How does tunable consistency work?**

A: Each query specifies a consistency level. For writes: ANY (1 node), ONE, QUORUM (majority), ALL. For reads: ONE, QUORUM, ALL. The key formula for strong consistency: R + W > RF. If write_quorum + read_quorum > replication_factor, the read and write quorums must overlap by at least one node (pigeonhole principle). That overlap node always has the latest write, so the coordinator returns correct data. For RF=3, Q=2: 2+2=4 > 3 → strong consistency.

**Q: Why shouldn't you use secondary indexes in Cassandra?**

A: Secondary indexes are local per-node, not global. A query using a secondary index requires the coordinator to contact every node in the cluster to check its local index. This scatter-gather pattern has unpredictable latency and doesn't scale. Instead, denormalize: create a separate table whose partition key is the field you want to query by. This gives you a single-partition lookup (fast, predictable).

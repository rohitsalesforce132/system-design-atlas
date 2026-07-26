# Apache Kafka — The Complete Deep Dive

> Everything you need to know about Kafka: from internal architecture to partitioning strategies to consumer group mechanics to production operations. This is how Netflix, Uber, LinkedIn, and Flipkart process trillions of events per day.

---

## Table of Contents

1. [What Problem Kafka Solves](#the-problem)
2. [Mental Model: Kafka Is a Distributed Log](#mental-model)
3. [Core Architecture](#architecture)
4. [Topics, Partitions, and Offsets](#topics)
5. [Producers — How Writes Work](#producers)
6. [Consumers and Consumer Groups](#consumers)
7. [Replication and ISR (In-Sync Replicas)](#replication)
8. [Delivery Semantics — At-Least-Once, Exactly-Once](#delivery)
9. [Kafka Internals: Log Segments, Index Files](#internals)
10. [Partitioning Strategies](#partitioning-strategies)
11. [Kafka Streams and ksqlDB](#streams)
12. [Kafka vs Alternatives](#alternatives)
13. [How Real Companies Use Kafka](#real-companies)
14. [How YOU Can Build This](#build)
15. [Common Interview Questions](#interview)

---

<a id="the-problem"></a>
## What Problem Kafka Solves

### The Integration Nightmare

Before Kafka, systems talked to each other **point-to-point**. Each system needed a custom integration with every other system:

```
WITHOUT Kafka (Point-to-Point Integration):

  Analytics ←─── custom pipe ──── User Service
  Analytics ←─── custom pipe ──── Order Service
  Analytics ←─── custom pipe ──── Payment Service
  Email     ←─── custom pipe ──── User Service
  Email     ←─── custom pipe ──── Order Service
  Search    ←─── custom pipe ──── Product Service
  Search    ←─── custom pipe ──── Inventory Service
  Fraud     ←─── custom pipe ──── Payment Service
  Fraud     ←─── custom pipe ──── Order Service

  For N systems: N × (N-1) / 2 connections
  10 systems = 45 custom integrations
  50 systems = 1,225 custom integrations

  → Each integration is custom code
  → Each has different error handling
  → Adding a new consumer requires modifying producers
  → Scaling is a nightmare
  → If a consumer is slow, the producer is blocked
```

### With Kafka: Central Nervous System

```
WITH Kafka (Pub/Sub Hub):

  User Service ──┐
  Order Service ──┤──► KAFKA ──┬──► Analytics
  Payment Service ─┤            ├──► Email Service
  Product Service ─┘            ├──► Search Indexer
                                ├──► Fraud Detection
                                ├──>> Data Warehouse (S3)
                                └──>> Notification Service

  Producers don't know (or care) who consumes their events.
  Consumers don't know (or care) who produces events.
  Kafka decouples them completely.
```

### What Kafka Gives You

```
1. Decoupling:    Producers and consumers don't know about each other.
2. Durability:    Events persist on disk. Consumers can be down for hours
                  and still process every event when they come back.
3. Replay:        Consumers can re-read old events (unlike traditional queues).
4. Ordering:      Events within a partition are strictly ordered.
5. Scale:         Millions of events per second across a cluster.
6. Multiple consumers: Multiple independent consumers can read the same events.
```

---

<a id="mental-model"></a>
## Mental Model: Kafka Is a Distributed Log

### The Key Insight

**Kafka is not a traditional message queue.** It's a **distributed append-only log**.

```
Traditional Queue (RabbitMQ, SQS):
  Producer ──► [Queue] ──► Consumer
                    │
                    └── Message is DELETED after consumption

  → Once consumed, message is GONE.
  → Can't re-read.
  → Only ONE consumer per message.

Kafka (Distributed Log):
  Producer ──► [Log] ────► Consumer A (reads from offset 0)
                │       ──► Consumer B (reads from offset 5)
                │       ──► Consumer C (reads from offset 100)
                │
                └── Message STAYS in the log for days/weeks/months

  → Multiple consumers can read the SAME events independently.
  → Consumers can rewind and re-read.
  → Messages are NOT deleted after consumption.
  → Retained by retention policy (default: 7 days).
```

### The Log Analogy

```
Imagine a ledger book in a library:

  Page 0:   "User Alice logged in at 10:00"
  Page 1:   "User Bob placed order #123 at 10:01"
  Page 2:   "Payment received for order #123 at 10:02"
  Page 3:   "User Carol signed up at 10:03"
  Page 4:   "Order #123 shipped at 10:15"
  ...

  Reader A (Analytics): "I'm reading from page 0, currently on page 3"
  Reader B (Email):     "I'm reading from page 2, currently on page 4"
  Reader C (Fraud):     "I just started, reading from page 0"

  Each reader has their OWN bookmark (offset).
  The ledger doesn't change when someone reads it.
  New entries are always appended at the end.
  Old pages are shredded after 7 days (retention policy).
```

---

<a id="architecture"></a>
## Core Architecture

### High-Level View

```
                    ┌──────────────────────────────────┐
                    │         KAFKA CLUSTER             │
                    │                                  │
  Producer ──────►  │  ┌──────┐  ┌──────┐  ┌──────┐  │  ──────► Consumer
                    │  │Broker│  │Broker│  │Broker│  │  ──────► Consumer
  (writes events)   │  │  1   │  │  2   │  │  3   │  │  (reads events)
                    │  └──────┘  └──────┘  └──────┘  │
                    │       │        │        │       │
                    │       └────────┴────────┘       │
                    │          (replication)          │
                    │                                  │
                    │  ┌──────────────────────────┐   │
                    │  │     ZooKeeper / KRaft     │   │
                    │  │  (metadata, leader         │   │
                    │  │   election, configuration) │   │
                    │  └──────────────────────────┘   │
                    └──────────────────────────────────┘
```

### Component Breakdown

| Component | What It Does | Analogy |
|-----------|-------------|---------|
| **Broker** | A single Kafka server. Stores data, serves reads/writes. | A filing cabinet |
| **Cluster** | Multiple brokers working together. | A warehouse of filing cabinets |
| **Topic** | A named stream of events (like a category). | A labeled shelf in the cabinet |
| **Partition** | A topic split into multiple parallel streams. | Individual drawers in the shelf |
| **Producer** | Application that writes events to Kafka. | A clerk who writes entries |
| **Consumer** | Application that reads events from Kafka. | A reader who reads entries |
| **Consumer Group** | A team of consumers sharing the work. | A team of readers dividing pages |
| **ZooKeeper/KRaft** | Manages cluster metadata and leader election. | The warehouse manager |

### Broker Details

```
A single Kafka broker:

  ┌──────────────────────────────────────────────┐
  │  Broker 1                                     │
  │                                              │
  │  Disk Storage:                               │
  │  /var/lib/kafka/data/                        │
  │  ├── topic-orders-0/     (partition 0 data)  │
  │  │   ├── 00000000000000000000.log  (segment) │
  │  │   ├── 00000000000000000000.index          │
  │  │   └── 00000000000000000000.timeindex      │
  │  ├── topic-orders-1/     (partition 1 data)  │
  │  │   └── ...                                │
  │  └── topic-payments-0/  (different topic)    │
  │      └── ...                                │
  │                                              │
  │  RAM:                                        │
  │  ├── Page Cache (OS-managed, used for        │
  │  │   buffering reads/writes — Kafka's        │
  │  │   biggest performance secret)             │
  │  │                                           │
  │  └── JVM Heap (~6GB, keeps metadata          │
  │      and consumer group state)               │
  │                                              │
  │  Network:                                    │
  │  ├── Bounded by NIC speed (10GbE typical)    │
  │  └── Zero-copy sends (disk → network,        │
  │      bypassing CPU/RAM)                      │
  └──────────────────────────────────────────────┘
```

### ZooKeeper / KRaft (Controller)

```
ZooKeeper manages cluster metadata:

  ┌──────────────────┐
  │  ZooKeeper        │
  │  Ensemble (3-5    │
  │  nodes)           │
  │                  │
  │  Stores:         │
  │  ├── Which broker is alive?          │
  │  ├── Which broker is leader for      │
  │  │   each partition?                 │
  │  │                                  │
  │  └── Elects the controller broker    │
  │      (manages partition assignment)  │
  └──────────────────┘

KRaft (Kafka Raft — replacing ZooKeeper):
  Kafka 2.8+ can use KRaft mode (built-in Raft consensus)
  → No external ZooKeeper needed
  → Simpler deployment
  → Faster metadata operations
```

---

<a id="topics"></a>
## Topics, Partitions, and Offsets

### Topic

A topic is a named stream of events. Think of it as a **category** or **feed name**.

```
Topics in an e-commerce system:

  topic: "user-events"     → login, signup, profile_update
  topic: "order-events"    → order_created, order_cancelled, order_shipped
  topic: "payment-events"  → payment_initiated, payment_completed, payment_failed
  topic: "product-events"  → product_added, price_changed, stock_updated
```

### Partition

A topic is split into **partitions** for parallelism. Each partition is an ordered, append-only log.

```
Topic: "order-events" (3 partitions)

  Partition 0:  [order_001] [order_004] [order_007] [order_010]
  Partition 1:  [order_002] [order_005] [order_008] [order_011]
  Partition 2:  [order_003] [order_006] [order_009] [order_012]

  How orders are assigned to partitions:
    partition = hash(order_id) % 3

    hash("order_001") % 3 = 0  → Partition 0
    hash("order_002") % 3 = 1  → Partition 1
    hash("order_003") % 3 = 2  → Partition 2
    hash("order_004") % 3 = 0  → Partition 0

  Within each partition, order is STRICTLY preserved.
  Across partitions, order is NOT guaranteed.
```

### Why Partitions Exist

```
1. PARALLELISM:
   Each partition can be read by a different consumer in parallel.
   3 partitions = 3 consumers reading simultaneously = 3x throughput.

2. SCALING:
   More partitions = more parallel consumers = higher throughput.
   Kafka can handle 10,000+ partitions per cluster.

3. LOCALITY:
   Events for the same key always go to the same partition.
   All events for "user_id=123" are in one partition, in order.
```

### Offset

Each message in a partition has a unique, monotonically increasing **offset**.

```
Partition 0:
  Offset:  0          1          2          3          4
  Event:  [order_001] [order_004] [order_007] [order_010] [order_013]
                                        ^
                                        Consumer A is here (offset = 2)
           ^
           Consumer B is here (offset = 0, just started)

  Offsets are:
    - Monotonically increasing (never decrease)
    - Assigned by Kafka (not by producer)
    - Unique within a partition (not across partitions)
    - Sequential integers (0, 1, 2, 3, ...)
```

### Key Properties of Offsets

```
1. IMMUTABLE: An offset, once written, never changes.
   → Message at offset 5 will ALWAYS be at offset 5.

2. SEQUENTIAL: Offsets are gap-free (0,1,2,3...).
   → If offset 10 exists, offsets 0-9 also exist.

3. CONSUMER-CONTROLLED: The consumer controls its offset.
   → Kafka doesn't track which messages were "consumed."
   → The consumer commits its offset periodically.
   → Consumer can rewind: "Start reading from offset 0 again."

4. COMMITTED: Consumer saves its position (offset) to Kafka
   → Stored in a special topic: __consumer_offsets
   → If consumer crashes and restarts, it resumes from committed offset
```

---

<a id="producers"></a>
## Producers — How Writes Work

### Producer Flow

```
Producer App                    Kafka Cluster
    │                               │
    │  1. Serialize event           │
    │  2. Determine partition       │
    │  3. Batch events              │
    │  4. Send to partition leader  │
    │                               │
    │──── write to partition 0 ────►│ (Partition 0 Leader = Broker 1)
    │                               │
    │◄─── response (offset) ────────│
    │                               │
```

### How the Producer Determines the Partition

```
Three strategies:

1. NO KEY (round-robin):
   Producer sends events in round-robin: P0, P1, P2, P0, P1, P2...
   → Even distribution. No ordering guarantees.

2. KEY specified (hash-based):
   partition = hash(key) % num_partitions

   key = "user_id:123" → hash("user_id:123") % 3 = 1 → Partition 1
   → All events for user 123 go to Partition 1.
   → Order preserved for this user.

3. CUSTOM PARTITIONER:
   Producer has custom logic (e.g., route by geography).
   → Partition by region: India→P0, US→P1, EU→P2
```

### Producer Batching

```
Producer accumulates events before sending:

  Time ──────────────────────────────────────────────►
       0ms   1ms   2ms   3ms   ...   20ms
       │     │     │     │              │
       │  e1  │  e2  │  e3             │
       │     │     │     │              │
       ▼     ▼     ▼     ▼              ▼
  [Batch Buffer]
  [e1] [e2] [e3] ............ [e20]
                              │
                              ▼
                       Send batch to Kafka
                       (batch.size = 16KB or linger.ms = 20ms, whichever first)

Why batch?
  - Network efficiency: One request with 20 events vs 20 requests with 1 event
  - Higher throughput: Kafka writes batch sequentially (faster disk I/O)
  - Lower CPU: One serialization pass per batch

Trade-off:
  - Higher latency (wait for batch to fill): First event waits up to 20ms
  - Higher throughput: 10-100x more events per second
```

### Producer acks (Acknowledgment Levels)

```
acks=0 (Fire and forget):
  Producer sends and doesn't wait for ANY acknowledgment.
  → Maximum throughput.
  → Can lose data if broker crashes.
  → Use for: metrics, logs where occasional loss is OK.

acks=1 (Leader acknowledgment):
  Producer waits for the LEADER to write the event.
  → Leader writes to local log → acknowledges.
  → If leader crashes AFTER acknowledging but BEFORE replicating → data loss.
  → Good balance of safety and performance.

acks=all (or acks=-1) (Full acknowledgment):
  Producer waits for LEADER + ALL in-sync replicas.
  → Safest. No data loss as long as min.insync.replicas are up.
  → Slowest (waits for all replicas).
  → Use for: financial data, critical events.

  acks=all with min.insync.replicas=2:
  → Leader + at least 1 replica must acknowledge
  → If fewer than min.insync.replicas are alive → producer gets error
  → Prevents writing to an under-replicated partition
```

### acks=all Visualized

```
Producer ──► Partition 0 Leader (Broker 1)
                │
                ├── Writes to local log ✓
                │
                │ Replicate to:
                ├──► Broker 2 (Follower 1) ──► writes ✓ ──► ack
                └──► Broker 3 (Follower 2) ──► writes ✓ ──► ack

  All acknowledged? → Leader sends ack to producer
  Producer receives ack → confirms event is durable
```

---

<a id="consumers"></a>
## Consumers and Consumer Groups

### Consumer Basics

A consumer reads events from a partition, starting from a specific offset.

```
Consumer reads Partition 0:

  Offset:  0       1       2       3       4       5
  Data:   [e0]   [e1]   [e2]   [e3]   [e4]   [e5]
                                   ^
                                   Consumer reads from here

  Consumer says: fetch(offset=3)
  → Kafka returns [e3, e4, e5] (batch)
  → Consumer processes e3, e4, e5
  → Consumer commits offset = 6
  → Next fetch starts from offset 6
```

### Consumer Groups — The Magic of Kafka

A consumer group is a set of consumers that **divide the work** of reading a topic.

```
Topic: "order-events" (3 partitions)

Consumer Group "analytics" (3 consumers):

  Partition 0 ──► Consumer A
  Partition 1 ──► Consumer B
  Partition 2 ──► Consumer C

  Each partition is read by EXACTLY ONE consumer in the group.
  → No duplicate processing.
  → 3x throughput (3 consumers in parallel).

Another Consumer Group "email" (2 consumers):

  Partition 0 + 1 ──► Consumer X (gets 2 partitions because only 2 consumers)
  Partition 2     ──► Consumer Y

  Independent from "analytics" group.
  → Both groups read ALL events, independently.
```

### The Golden Rule: Partitions vs Consumers

```
RULE: You can never have more active consumers than partitions in a group.

  Topic has 3 partitions:
    3 consumers → each gets 1 partition. All active. ✓
    2 consumers → one gets 2 partitions, other gets 1. ✓
    1 consumer  → gets all 3 partitions. ✓
    4 consumers → 3 are active, 1 is IDLE. ✗ (wasted consumer)

  LESSON: Plan partition count based on desired parallelism.
  Need 10 parallel consumers? Create at least 10 partitions.
```

### Rebalance — When Consumers Join or Leave

```
Initial state:
  Consumer Group "analytics": [A, B, C]
  Partition 0 ──► A
  Partition 1 ──► B
  Partition 2 ──► C

Consumer B crashes:
  → Group detects B is gone (missed heartbeats)
  → REBALANCE triggered
  → Partitions reassigned:
    Partition 0 ──► A
    Partition 1 ──► C  (C now handles 2 partitions)
    Partition 2 ──► C

New Consumer D joins:
  → REBALANCE triggered
  → Partitions reassigned for even distribution:
    Partition 0 ──► A
    Partition 1 ──► D  (new consumer takes over P1)
    Partition 2 ──► C

During rebalance: ALL consumers STOP processing for a few seconds.
→ This is called the "stop-the-world" rebalance.
→ Modern Kafka (2.4+) uses incremental rebalancing to minimize disruption.
```

### Offset Commit Strategies

```
Auto-commit (enable.auto.commit=true):
  Consumer automatically commits offset every 5 seconds.
  → Simple.
  → Risk: If consumer processes events but crashes before next auto-commit,
    those events are re-processed (at-least-once).

Manual commit (after processing):
  consumer.subscribe(['order-events'])
  for message in consumer:
      process_order(message.value)   ← Do the work
      consumer.commit_sync()          ← Then commit offset

  → Safe: Offset only committed after successful processing.
  → Risk: If process_order() crashes, event is re-processed on restart.
  → Consumer must be idempotent (processing twice = same result).
```

---

<a id="replication"></a>
## Replication and ISR (In-Sync Replicas)

### Why Replication

```
Without replication:
  Broker 1 holds Partition 0
  Broker 1 crashes → Partition 0 is OFFLINE. Data inaccessible.
  → Single point of failure.

With replication (Replication Factor = 3):
  Partition 0 has 3 copies:
    Leader:   Broker 1  (handles all reads/writes)
    Follower: Broker 2  (copies from leader)
    Follower: Broker 3  (copies from leader)

  Broker 1 crashes → Broker 2 becomes new leader → No downtime.
```

### Leader and Followers

```
  Producer/Consumer
       │
       ▼
  ┌──────────────┐
  │  Leader       │  ←── Handles ALL reads and writes
  │  (Broker 1)   │      for this partition
  └──────┬───────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  ┌──────┐ ┌──────┐
  │Follower│ │Follower│  ←── Copy data from leader
  │(Broker │ │(Broker │      (do NOT serve clients)
  │  2)    │ │  3)    │
  └──────┘ └──────┘

  Only the leader serves clients.
  Followers just replicate.
  If leader dies → a follower is promoted to leader.
```

### ISR (In-Sync Replicas)

```
ISR = set of followers that are caught up with the leader.

  Leader (offset 1000):
    ├── Follower B (offset 999) → IN SYNC ✓ (within threshold)
    └── Follower C (offset 980) → OUT OF SYNC ✗ (too far behind)

  ISR = {Leader, B}  (C is not in ISR)

  Why ISR matters:
    → If leader dies, only ISR members are eligible to become new leader.
    → C (not in ISR) will NOT become leader (it's missing data).
    → This prevents data loss.

  If too many followers fall out of ISR:
    → min.insync.replicas check fails
    → acks=all writes are rejected (to protect data)
```

### Leader Election

```
Leader (Broker 1) crashes:

  1. ZooKeeper/KRaft detects broker failure
  2. ISR list is checked: {Broker 1 (dead), Broker 2}
  3. Broker 2 is the only ISR member → becomes new leader
  4. Consumers/producers reconnect to Broker 2

  Election time: ~seconds (ZooKeeper based) or <1s (KRaft)
```

### Unclean Leader Election

```
What if ALL ISR members die, but non-ISR followers are alive?

  Leader (Broker 1): DEAD
  Follower B:        DEAD (was in ISR)
  Follower C:        ALIVE (was NOT in ISR, missing last 20 events)

  unclean.leader.election.enable = true:
    → C becomes leader despite missing data
    → LAST 20 EVENTS ARE LOST
    → Availability preferred over consistency

  unclean.leader.election.enable = false:
    → Partition goes OFFLINE until an ISR member returns
    → No data loss, but unavailable
    → Consistency preferred over availability

  Financial systems: unclean=false (never lose data)
  Analytics systems: unclean=true (availability > data)
```

---

<a id="delivery"></a>
## Delivery Semantics

### Three Delivery Guarantees

```
1. AT-MOST-ONCE:
   Each message is delivered 0 or 1 times. May be lost. Never duplicated.

   How: acks=0, auto-commit before processing
   Use: Metrics where losing some is OK

2. AT-LEAST-ONCE (Default Kafka behavior):
   Each message is delivered 1 or more times. Never lost. May be duplicated.

   How: acks=all, manual commit after processing
   Use: Most applications. Consumer must be idempotent.

3. EXACTLY-ONCE (Hard, but Kafka supports it):
   Each message is delivered exactly 1 time. Never lost. Never duplicated.

   How: Kafka Transactions (idempotent producer + transactional consumer)
   Use: Financial transactions
```

### How Exactly-Once Works in Kafka

```
Without exactly-once:
  Producer writes to Kafka → Consumer reads → writes result to database
  → If consumer crashes after processing but before committing offset
  → On restart, event is re-processed → database has duplicate write

With exactly-once (Kafka Transactions):
  1. Producer starts transaction
  2. Consumer reads event
  3. Producer writes output event(s) to another Kafka topic
  4. Consumer commits offset
  5. Producer commits transaction

  ALL OF STEPS 2-5 are ATOMIC. Either all happen or none.

  If crash at step 4: Transaction aborts. Offset not committed.
  → On restart, event is re-read and re-processed.
  → But the output write was also aborted → no duplicate.

  This only works within Kafka (topic-to-topic).
  For external systems (databases), you still need idempotent consumers.
```

### Idempotent Producer

```
Normal producer:
  Producer sends event → network hiccup → no ack received
  → Producer retries → sends again
  → Kafka has TWO copies of the event!

Idempotent producer (enable.idempotence=true):
  Producer attaches a Producer ID (PID) + sequence number to each event

  Event 1: PID=42, seq=0
  Event 2: PID=42, seq=1
  Event 3: PID=42, seq=2

  If Event 2 is retried: PID=42, seq=1
  → Kafka sees: "I already have seq=1 for PID=42"
  → Kafka DEDUPLICATES automatically
  → No duplicate write
```

---

<a id="internals"></a>
## Kafka Internals: Log Segments, Index Files

### How Kafka Stores Data on Disk

```
Each partition is a directory on disk.
Inside, data is stored in SEGMENTS (files).

/var/lib/kafka/data/order-events-0/
  ├── 00000000000000000000.log      ← Active segment (writing here)
  ├── 00000000000000000000.index    ← Offset index
  ├── 00000000000000000000.timeindex ← Timestamp index
  ├── 00000000000000050000.log      ← Older segment (sealed)
  ├── 00000000000000050000.index
  └── leader-epoch-checkpoint

Naming convention: The filename is the offset of the FIRST message.
  00000000000000000000.log → starts at offset 0
  00000000000000050000.log → starts at offset 50000
```

### Log Segment Details

```
ACTIVE SEGMENT (currently being written to):
  00000000000000000000.log

  Offset   Timestamp     Key           Value
  ─────    ──────────    ──────────    ──────────────
  0        10:00:00.000  order_001     {"item":"book","amt":500}
  1        10:00:00.100  order_002     {"item":"pen","amt":50}
  2        10:00:00.200  order_003     {"item":"laptop","amt":50000}
  ...
  49999    10:05:32.100  order_50000   {"item":"phone","amt":10000}

  When segment reaches segment.bytes (default 1GB) OR segment.ms (default 7 days):
  → Segment is SEALED (closed, read-only)
  → New segment created: 0000000000000050000.log

SEALED SEGMENT:
  00000000000000050000.log
  → Can be deleted when retention expires
  → Can be compacted (if topic uses compaction)
```

### Index Files — Fast Offset Lookup

```
Without an index:
  Consumer: "Give me offset 49999"
  → Kafka must scan the entire log file from the beginning
  → For a 1GB segment, that's slow.

With an index:
  Consumer: "Give me offset 49999"
  → Kafka looks up index: offset 49999 → byte position 987654
  → Kafka seeks directly to byte 987654 in the log file
  → O(1) lookup, regardless of segment size.

INDEX FILE:
  00000000000000000000.index (sparse index — not every offset):

  Offset    Byte Position
  ──────    ─────────────
  0         0
  100       12450
  500       62300
  1000      124800
  ...
  49000     987000

  (Sparse = doesn't store every offset, only every ~4KB of data)
  → Memory efficient (an index for 1GB segment is only ~20KB)
  → Lookup: binary search in index → seek in log file → scan a few records
```

### Why Kafka Is So Fast

```
1. SEQUENTIAL I/O (not random I/O):
   Kafka only appends to the end of the log. Never updates in place.
   Sequential disk writes are 6x faster than random writes on SSD,
   and 100x+ faster on HDD.

   Sequential write to SSD:  ~600 MB/s
   Random write to SSD:       ~100 MB/s
   Sequential write to HDD:   ~100 MB/s
   Random write to HDD:         ~1 MB/s

2. PAGE CACHE (Linux OS-managed):
   Kafka doesn't cache data in the JVM heap.
   Instead, it relies on Linux's page cache.
   → When Kafka writes to disk, it actually writes to OS page cache.
   → OS flushes to physical disk asynchronously.
   → Reads come from page cache (RAM) if recently written.
   → A Kafka broker with 32GB RAM can cache ~28GB of data in page cache.

3. ZERO-COPY (sendfile):
   Traditional read + send:
     Disk → Kernel buffer → User buffer (Kafka) → Socket buffer → Network
     (4 copies, 2 context switches)

   Zero-copy (sendfile):
     Disk → Kernel buffer → Network
     (2 copies, 0 context switches)
     → Data bypasses user space entirely
     → 2-3x faster for large transfers

4. BATCHING:
   Producer batches events → fewer network requests
   Consumer batches reads → fewer disk reads
   Batch compression → less network/disk usage
```

---

<a id="partitioning-strategies"></a>
## Partitioning Strategies

### Choosing the right partition key is critical. It determines:

```
1. Ordering guarantees
2. Load distribution
3. Consumer parallelism
```

### Strategy 1: Round-Robin (No Key)

```
Producer sends without a key:
  Event 1 → Partition 0
  Event 2 → Partition 1
  Event 3 → Partition 2
  Event 4 → Partition 0
  ...

  Pro: Perfectly even distribution.
  Con: No ordering guarantees whatsoever.
  Use: Independent events (metrics, logs).
```

### Strategy 2: Entity ID Key

```
Producer sends with user_id as key:
  key="user:100" → hash("user:100") % 3 = 1 → Partition 1
  key="user:100" → same hash → Partition 1 (always!)
  key="user:101" → hash("user:101") % 3 = 0 → Partition 0

  Pro: All events for a specific user are ordered (same partition).
  Con: Some users may generate more events → hot partitions.
  Use: User activity streams, per-entity processing.
```

### Strategy 3: Compound Key

```
key = "user_id:session_id"

  All events within a session are ordered.
  Different sessions for the same user go to different partitions.

  Pro: Finer-grained ordering.
  Con: More complex.
  Use: Session-based analytics.
```

### The Hot Partition Problem

```
Problem: One key generates disproportionate traffic.

  key="celebrity_id" → always goes to Partition 2
  80% of all events are for this celebrity
  → Partition 2 is overloaded while P0 and P1 are idle.

Solutions:
  1. Add randomness: key = "celebrity_id:random_number"
     → Spreads across partitions. But loses ordering.

  2. Separate topic for hot keys:
     → "celebrity-events" topic with more partitions.

  3. Sticky partitioning (Kafka 2.4+):
     → Producer batches all keys together, then switches partitions.
     → Better batching without sacrificing key ordering.
```

### How Many Partitions Do You Need?

```
Formula:
  partitions = max(target_throughput / consumer_throughput, required_parallelism)

Example:
  Target throughput: 100,000 events/sec
  One consumer processes: 5,000 events/sec
  → Need 20 partitions (100,000 / 5,000)

  Also: If you want 10 parallel consumers → need ≥ 10 partitions

  Take the MAX of both.

Guidelines:
  - Start with 6-12 partitions for a new topic
  - Under-provision rather than over-provision
  - Adding partitions later is possible but breaks key ordering
    (existing keys may remap to different partitions)
  - Kafka performance degrades above ~10,000 partitions per broker
```

---

<a id="streams"></a>
## Kafka Streams and ks

qlDB

### Kafka Streams

A Java library for processing Kafka data in real-time. Not a separate cluster — it runs inside your application.

```
Traditional stream processing:
  Kafka → Spark Streaming / Flink → Kafka/Database
  (Separate cluster to manage)

Kafka Streams:
  Your Java app reads from Kafka, processes, writes back to Kafka.
  (No separate cluster. Just a library.)

Example: Count orders by product

  KStream<String, Order> orders = builder.stream("order-events");

  KTable<String, Long> productCounts = orders
      .groupBy((key, order) -> order.getProductId())
      .count();

  productCounts.toStream().to("product-counts");
```

### Key Concepts

```
KStream:  An event stream. Each record is an independent event.
          (e.g., "order placed", "payment received")

KTable:   A changelog. Each record updates the state for a key.
          (e.g., "current balance for user X", "current stock for product Y")

          KTable for "user-balance":
            user:1 → 1000 (initial)
            user:1 → 800  (spent 200)
            user:1 → 900  (received 100)

          Reading KTable at any time → latest value per key.

GlobalKTable: Entire table replicated on each instance.
          For small reference data (e.g., product catalog).
```

### Windowed Operations

```
Count orders per 5-minute window:

  10:00 ──► [3 orders]
  10:05 ──► [7 orders]
  10:10 ──► [2 orders]

  KTable<Windowed<String>, Long> windowedCounts = orders
      .groupByKey()
      .windowedBy(TimeWindows.of(Duration.ofMinutes(5)))
      .count();

Window types:
  Tumbling:    Fixed-size, non-overlapping (0-5min, 5-10min, 10-15min)
  Hopping:     Fixed-size, overlapping (0-5min, 2-7min, 4-9min)
  Session:     Variable-size based on activity gaps
```

### ksqlDB

```
SQL interface for Kafka:

  CREATE STREAM orders (
      order_id VARCHAR,
      user_id VARCHAR,
      amount DECIMAL
  ) WITH (
      KAFKA_TOPIC='order-events',
      VALUE_FORMAT='JSON'
  );

  CREATE TABLE product_counts AS
      SELECT product_id, COUNT(*) as count
      FROM orders
      GROUP BY product_id
      EMIT CHANGES;

  → Continuous query. Never stops. Results update in real-time.
```

---

<a id="alternatives"></a>
## Kafka vs Alternatives

### Comparison Table

| Feature | Kafka | RabbitMQ | SQS | Redis Streams | Pulsar |
|---------|-------|---------|-----|---------------|--------|
| **Retention** | Days/weeks/months | Deleted on consume | Deleted on consume | Days (configurable) | Days/weeks/months |
| **Replay** | Yes (rewind offset) | No | No | Yes | Yes |
| **Multiple consumers** | Yes (consumer groups) | Limited | No | Yes | Yes |
| **Throughput** | Millions/sec | ~50K/sec | ~3K/sec | ~100K/sec | Millions/sec |
| **Ordering** | Per partition | Per queue | FIFO queue | Per stream | Per partition |
| **Persistence** | Disk | Disk or RAM | Disk | RAM (optionally disk) | Disk (BookKeeper) |
| **Streaming API** | Kafka Streams | No | No | Limited | Functions |
| **Latency** | ~5-50ms | ~1-10ms | ~10-100ms | <1ms | ~5-50ms |
| **Multi-tenancy** | No native | No | Yes | No | Yes |
| **Best for** | Event streaming, analytics | Task queues, RPC | Cloud queue | Simple stream | Large-scale streaming |

### When to Choose Kafka

```
✅ Choose Kafka when:
  - You need event streaming (not just queuing)
  - Multiple consumers need to read the same events
  - You need to replay events
  - Throughput is high (>50,000 events/sec)
  - You need stream processing (Kafka Streams)
  - Events must be retained for days/weeks

❌ Don't choose Kafka when:
  - You need a simple task queue (use RabbitMQ)
  - Throughput is low (use Redis Streams or RabbitMQ)
  - You need per-message routing (use RabbitMQ)
  - You need sub-millisecond latency (use Redis Streams)
  - You need complex routing logic (use RabbitMQ)
  - Just need to decouple two services (use SQS)
```

---

<a id="real-companies"></a>
## How Real Companies Use Kafka

### LinkedIn (Created Kafka)

```
LinkedIn's Kafka Usage:

  7 trillion messages per day
  100,000+ topics
  ~100,000 partitions
  1,500+ brokers

  Topics:
  ├── User activity (page views, clicks, searches)
  ├── System metrics (CPU, memory, latency)
  ├── Database change events (CDC)
  ├── Audit logs
  └── Inter-service communication

  Consumers:
  ├── Stream processing (Apache Samza)
  ├── ETL to data warehouse (Hadoop/Spark)
  ├── Real-time dashboards
  └── Notification services
```

### Netflix

```
Netflix's Kafka Usage:

  7 trillion messages per day
  700+ billion events/day
  500+ topics
  1,000+ consumer applications

  Architecture:
  Microservice ──► Kafka ──► Schema Registry (Avro schemas)
                   │
                   ├──► Elasticsearch (search indexing)
                   ├──► S3 (data lake for analytics)
                   ├──► Flink (real-time alerting)
                   └──>> Druid (real-time dashboards)

  Key design:
  - Schema Registry: every event has an Avro schema
  - Producers register schema → Consumers discover schema
  - Breaking schema changes → schema evolution rules
```

### Uber

```
Uber's Kafka Usage:

  2,000+ topics
  ~2 trillion messages/day
  100+ clusters globally
  Multi-region

  Topics:
  ├── trip-events (every trip state change)
  ├── driver-locations (GPS every 4 seconds)
  ├── pricing-updates (surge pricing recalculation)
  └── marketplace-events (driver-rider matching)

  Architecture:
  Driver app ──► Gateway ──► Kafka ──► Consumer services
  (GPS update)               │
                             ├──► Surge Pricing (real-time)
                             ├──>> Rider App (nearby drivers)
                             ├──► Trip matching
                             └──>> Analytics (S3 → Spark)
```

### Flipkart (Big Billion Days)

```
Flipkart's Kafka Usage:

  During Big Billion Days:
  - ~100,000 events/sec (peak)
  - Topics: order-events, inventory-events, payment-events
  - Kafka buffers traffic spikes between microservices

  Architecture:
  Checkout Service ──► Kafka ──► Inventory Service
  (high throughput)     (buffers)  (may be slower during sale)

  Key benefit: Kafka absorbs the spike.
  Even if Inventory Service can only process 10K/sec,
  Kafka buffers the excess and feeds it through gradually.
```

---

<a id="build"></a>
## How YOU Can Build This

### Level 1: Local Kafka (Docker)

```yaml
# docker-compose.yml
version: '3'
services:
  kafka:
    image: confluentinc/cp-kafka:7.5.0
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: 'broker,controller'
      KAFKA_LISTENERS: 'PLAINTEXT://0.0.0.0:9092'
      KAFKA_ADVERTISED_LISTENERS: 'PLAINTEXT://localhost:9092'
      KAFKA_CONTROLLER_QUORUM_VOTERS: '1@kafka:9093'
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: 'CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT'
      KAFKA_CONTROLLER_LISTENER_NAMES: 'CONTROLLER'
      KAFKA_INTER_BROKER_LISTENER_NAME: 'PLAINTEXT'
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      CLUSTER_ID: 'MkU3OEVBNTcwNTJENDM2Qk'
    ports:
      - 9092:9092
```

### Level 2: Python Producer + Consumer

```python
# Producer
from kafka import KafkaProducer
import json

producer = KafkaProducer(
    bootstrap_servers=['localhost:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks='all',  # Wait for all replicas
)

# Send events
producer.send('order-events', value={'order_id': 'ORD-001', 'amount': 500})
producer.send('order-events', value={'order_id': 'ORD-002', 'amount': 1200})
producer.flush()

# Consumer
from kafka import KafkaConsumer

consumer = KafkaConsumer(
    'order-events',
    bootstrap_servers=['localhost:9092'],
    group_id='analytics-service',
    auto_offset_reset='earliest',
    enable_auto_commit=False,
    value_deserializer=lambda x: json.loads(x.decode('utf-8')),
)

for message in consumer:
    print(f"Received: {message.value}")
    process_order(message.value)
    consumer.commit()  # Manual commit after processing
```

### Level 3: Production Architecture

```
  Microservices
  (Producers)
    │
    ▼
  Kafka Cluster (3-5 brokers)
  ├── Topic: order-events (12 partitions, RF=3)
  ├── Topic: payment-events (6 partitions, RF=3)
  ├── Topic: user-events (6 partitions, RF=3)
  │
  │ (Schema Registry validates event schemas)
  │
  ├──► Consumer Group: analytics
  │    └── Writes to S3 / Snowflake (batch ETL)
  │
  ├──► Consumer Group: email-service
  │    └── Sends confirmation emails
  │
  ├──► Consumer Group: fraud-detection
  │    └── Real-time ML inference
  │
  └──► Consumer Group: search-indexer
       └── Updates Elasticsearch
```

### Production Best Practices

| Practice | Why |
|----------|-----|
| Replication Factor = 3 | Survives 1 broker failure without data loss |
| min.insync.replicas = 2 | Requires at least leader + 1 replica for writes |
| acks = all | Ensures data is replicated before acknowledging |
| Partition count = desired consumer parallelism | Each consumer gets one partition |
| Producer with enable.idempotence=true | Prevents duplicate events on retry |
| Schema Registry | Enforces event schema compatibility |
| Monitoring: consumer lag | Alert if consumers fall behind producers |
| JMX metrics | Track broker health (CPU, disk, network) |
| Partition key = entity ID | Preserves order per entity |
| Compression (snappy or lz4) | 5-10x reduction in network/disk usage |

---

<a id="interview"></a>
## Common Interview Questions

**Q: How does Kafka achieve high throughput?**

A: Four key techniques:
1. **Sequential disk I/O** — append-only log, never update in place. Sequential writes are 6x faster than random writes on SSDs.
2. **Page cache** — Kafka relies on the OS page cache instead of JVM heap. A 32GB server can cache ~28GB of data in RAM.
3. **Zero-copy** — uses `sendfile()` syscall to send data directly from disk to network, bypassing user space. 2-3x faster.
4. **Batching** — producer batches events (batch.size=16KB or linger.ms=20ms). One large write instead of many small ones.

**Q: Explain partitions and consumer groups.**

A: A topic is split into partitions for parallelism. Each partition is an ordered, append-only log. A consumer group is a set of consumers that divide a topic's partitions among themselves — each partition is read by exactly one consumer in the group. If you have 3 partitions and 3 consumers, each consumer reads one partition (3x throughput). Multiple consumer groups can read the same topic independently. Key rule: you can't have more active consumers than partitions.

**Q: How does Kafka handle broker failure?**

A: Each partition has a leader and followers (replication factor, typically 3). All reads/writes go through the leader. Followers copy data from the leader. If the leader's broker dies: ZooKeeper/KRaft detects the failure, selects a new leader from the ISR (In-Sync Replicas), and clients reconnect to the new leader. If unclean.leader.election is enabled, a non-ISR follower can become leader (availability > data loss). If disabled, the partition goes offline until an ISR member returns (consistency > availability).

**Q: What is ISR?**

A: In-Sync Replicas. The set of replicas that are caught up with the leader (within `replica.lag.time.max.ms`, default 10 seconds). Only ISR members are eligible to become leader if the current leader fails. With `acks=all`, the producer waits for all ISR members to acknowledge. If ISR falls below `min.insync.replicas`, writes are rejected to prevent data loss.

**Q: How do you prevent duplicate events?**

A: Three layers:
1. **Idempotent producer** (`enable.idempotence=true`): Kafka attaches a Producer ID and sequence number to each event. If the producer retries, Kafka deduplicates based on sequence number.
2. **Transactional producer**: For read-process-write patterns. Producer reads from one topic, processes, writes to another — all within a transaction. Either all writes commit or none.
3. **Idempotent consumer**: Design consumers so processing the same event twice has no extra effect (e.g., database upsert with `ON CONFLICT DO UPDATE`).

**Q: How do you choose the number of partitions?**

A: Two factors:
1. **Throughput:** partitions ≥ target_throughput / per_consumer_throughput. If you need 100K events/sec and each consumer handles 5K/sec → 20 partitions.
2. **Parallelism:** partitions ≥ desired number of parallel consumers. If you want 10 consumers → at least 10 partitions.

Take the max. Start with 6-12 partitions for a new topic. It's possible to add partitions later but it breaks key ordering (existing keys may remap to different partitions).

**Q: What is consumer lag and why does it matter?**

A: Consumer lag = (latest offset on the partition) − (consumer's committed offset). It measures how far behind the consumer is. If a producer writes 1000 events/sec and the consumer processes only 500/sec, lag grows by 500/sec. If lag grows unboundedly, the consumer never catches up. Monitor consumer lag and alert when it exceeds a threshold. Fix by: adding more consumers (if partitions allow), optimizing consumer processing, or scaling the consumer vertically.

**Q: What happens when you add a new partition to an existing topic?**

A: Existing data is NOT redistributed. The new partition only receives new events. This means the partition key mapping changes: `hash(key) % N` becomes `hash(key) % (N+1)`. Events for an existing key may now go to a different partition, breaking ordering guarantees for that key. This is why partition count should be set correctly at creation and rarely changed.

**Q: Explain log compaction.**

```
Normal retention: Delete old segments after N days.
Compaction: Keep only the LATEST value for each key.

Before compaction:
  key=user:1, value={name:"Alice", version:1}   offset=0
  key=user:2, value={name:"Bob", version:1}     offset=1
  key=user:1, value={name:"Alice", version:2}   offset=2
  key=user:3, value={name:"Carol"}              offset=3
  key=user:1, value={name:"Alicia"}             offset=4

After compaction:
  key=user:2, value={name:"Bob"}                offset=1
  key=user:3, value={name:"Carol"}               offset=3
  key=user:1, value={name:"Alicia"}             offset=4

  → Only latest value per key is kept.
  → Older versions are deleted.
  → Key ordering is preserved.

Use case: State restoration. A consumer can rebuild its state by
reading the compacted topic from the beginning and getting the
latest value for each key.
```

**Q: Kafka vs RabbitMQ — how do you choose?**

A: Choose Kafka for: event streaming, multiple independent consumers, replay capability, high throughput (millions/sec), event retention, stream processing. Choose RabbitMQ for: task queues, complex routing (topic exchanges, header exchanges), point-to-point messaging, lower latency (<10ms), when messages should be deleted after consumption. Kafka is a distributed log; RabbitMQ is a traditional message broker. They solve different problems and many systems use both.

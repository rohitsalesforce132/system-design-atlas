# DynamoDB — The Complete Deep Dive

> Amazon runs DynamoDB for Prime Day traffic that hit 126 million requests per second in 2023. Airbnb stores millions of bookings on it. Amazon's own shopping cart runs on it. This guide covers how its partitioning, B-tree storage, secondary indexes, transactions, and Streams work inside — and why "managed" means you never think about a server.

---

## Table of Contents

1. [What Problem DynamoDB Solves](#the-problem)
2. [Architecture — How Partitioning Works Internally](#architecture)
3. [Data Model — Tables, Items, Keys](#data-model)
4. [How Reads Work — Consistency Models](#reads)
5. [Capacity Modes — Provisioned vs On-Demand](#capacity)
6. [Secondary Indexes — LSI vs GSI](#indexes)
7. [Transactions — ACID Across Multiple Items](#transactions)
8. [DynamoDB Streams — Change Data Capture](#streams)
9. [Global Tables — Multi-Region Replication](#global-tables)
10. [DAX — In-Memory Acceleration](#dax)
11. [TTL — Auto-Expiring Items](#ttl)
12. [Backups — PITR and On-Demand](#backups)
13. [How Real Companies Use DynamoDB](#real-apps)
14. [How YOU Can Build With It](#build)
15. [Common Interview Questions](#interview)

---

<a id="the-problem"></a>
## What Problem DynamoDB Solves

### The Operational Nightmare of Scaling a Database

```
You built an app. It uses PostgreSQL. It works great for 10,000 users.

Then you grow to 1 million users. Now you have problems:

  Problem 1: SHARDING
    → One PostgreSQL server can't handle the write load
    → You shard across 5 servers
    → NOW you must: pick a shard key, route queries, handle
      cross-shard joins, rebalance when a shard gets hot,
      deal with a node going down...

  Problem 2: REPLICATION
    → Set up primary + 2 replicas
    → Replication lag causes stale reads
    → Failover when primary dies (which replica wins?)
    → Set up proxy (PgBouncer) to hide this from the app

  Problem 3: OPS
    → Pager goes off at 3 AM: "disk 90% full on db-shard-3"
    → You must: add disk, run vacuum, monitor connections,
      tune autovacuum, patch security updates, backup strategy,
      point-in-time recovery setup...

  Problem 4: CAPACITY PLANNING
    → Black Friday is coming. Will the DB handle 10x traffic?
    → You over-provision "just in case" → pay for idle hardware
    → Or you under-provision → site crashes on the big day

EACH OF THESE IS A FULL-TIME JOB.
At scale, you need a dedicated DBA team just to keep the database alive.
```

### What DynamoDB Does Instead

```
DynamoDB says: "You worry about your app. We'll handle the database."

You create a table:
    aws dynamodb create-table --table-name Users ...

That's it. Behind the scenes, AWS handles:
  ✅ Partitioning (splits data across storage nodes automatically)
  ✅ Replication (3 copies across multiple AZs, automatically)
  ✅ Failover (a node dies → traffic rerouted, you never notice)
  ✅ Scaling (add capacity → takes effect in minutes)
  ✅ Backups (point-in-time recovery, on-demand snapshots)
  ✅ Patching, disk management, OS updates — ALL automatic
  ✅ Global replication (Global Tables — multi-region, managed)

YOU NEVER SEE A SERVER.
YOU NEVER SSH INTO ANYTHING.
YOU NEVER GET PAGED ABOUT DISK SPACE.

You just make API calls:
    PutItem, GetItem, Query, Scan
And DynamoDB handles the rest.
```

### What DynamoDB Optimizes For

```
1. ZERO OPERATIONS:    No servers to manage, patch, or scale
2. INFINITE SCALE:     Scales to any throughput / storage (Amazon
                        literally tested it at 126M requests/sec)
3. PREDICTABLE LATENCY: Single-digit ms reads/writes at ANY scale
4. INTEGRATION:        Native Lambda triggers, Streams, IAM auth
5. FULLY MANAGED HA:   3 AZ replication, 99.99% availability SLA

What it gives up:
  - No JOINs (you denormalize or use GSIs)
  - No complex ad-hoc queries (no GROUP BY, no aggregations)
  - No multi-table transactions beyond 100 items / 4 MB
  - Query flexibility limited to your key design
  - Vendor lock-in (it's AWS-only)

The trade: You give up query flexibility in exchange for
           a database that scales to infinity with zero ops.
```

### The Analogy

```
PostgreSQL is like owning a car:
  → You maintain it, fuel it, repair it, insure it.
  → Total control, total responsibility.
  → Great if you have a mechanic (DBA) on staff.

DynamoDB is like Uber:
  → You don't own, maintain, or even see the car.
  → You say "take me here" (make an API call).
  → You pay per trip (per request).
  → You can't customize the engine, but you also never
    get a flat tire at 3 AM.
```

---

<a id="architecture"></a>
## Architecture — How Partitioning Works Internally

You never see DynamoDB's servers, but understanding how it partitions data is the single most important thing for designing schemas that perform well.

### The Partition Key Hash

```
Every item in DynamoDB has a PARTITION KEY (also called "hash key").

When you write an item:
    PutItem(user_id="alice", name="Alice", ...)

DynamoDB computes:
    partition = hash(user_id) % total_partition_space

Specifically, DynamoDB uses an internal MD5-like hash function
that maps the partition key to a 0–3072 partition space:

    ┌──────────────────────────────────────────────────────┐
    │  PARTITION SPACE (0 to 3072)                          │
    │                                                       │
    │  0          512        1024       1536      3072     │
    │  │          │          │          │          │       │
    │  │ Partition│ Partition│ Partition│ Partition│       │
    │  │    A     │    B     │    C     │    D     │       │
    │  ▼          ▼          ▼          ▼          ▼       │
    │  [alice's   [bob's     [carol's   [dave's   [eve's  │
    │   data]      data]      data]      data]     data]  │
    └──────────────────────────────────────────────────────┘

  hash("alice") → token 342  → Partition A
  hash("bob")   → token 891  → Partition B
  hash("carol") → token 1204 → Partition C

  All data with partition key "alice" lives on Partition A.
  This is why "all data for one entity = one partition read" (fast).
```

### What's Inside a Partition

```
A partition is NOT a server. It's a logical unit of storage and
throughput backed by storage nodes. Each partition has:

  ┌─────────────────────────────────────────────────────┐
  │  PARTITION                                          │
  │                                                     │
  │  ┌───────────────────────────────────────────────┐ │
  │  │  B-TREE (sorted by partition key + sort key)   │ │
  │  │                                                │ │
  │  │  alice:0001 ──► {name, email, ...}            │ │
  │  alice:0002 ──► {name, email, ...}            │ │
  │  alice:0003 ──► {name, email, ...}            │ │
  │  └───────────────────────────────────────────────┘ │
  │                                                     │
  │  Capacity: up to 10 GB of data                     │
  │  Throughput: up to 3000 RCU / 1000 WCU (provisioned)│
  │  Replication: 3 copies across 3 Availability Zones │
  └─────────────────────────────────────────────────────┘

Inside each partition, items are stored in a B-TREE (not an LSM-tree
like Cassandra). This is a deliberate choice:
  → B-trees are great for reads (balanced tree, O(log n) lookup)
  → DynamoDB is read-optimized (most apps read more than they write)
  → B-trees support efficient range scans on the sort key
```

### Auto-Splitting Partitions

```
This is DynamoDB's killer feature. Partitions split AUTOMATICALLY.

Trigger 1: STORAGE LIMIT
  A partition approaches 10 GB of data.
  → DynamoDB splits it into TWO partitions.
  → Half the partition keys go to the new partition.

Trigger 2: THROUGHPUT LIMIT (provisioned mode)
  A partition is getting close to its 3000 RCU / 1000 WCU limit.
  → DynamoDB splits it to spread the load.

Trigger 3: MANUAL (on-demand mode)
  On-demand mode auto-scales throughput instantly to match traffic.

  ┌──────────────────────────────────────────────────────┐
  │  BEFORE SPLIT                                         │
  │                                                       │
  │  Partition A (11 GB — OVER LIMIT)                     │
  │  [alice, bob, carol, dave, eve, frank, grace, ...]    │
  │                                                       │
  │  AFTER AUTO-SPLIT                                     │
  │                                                       │
  │  Partition A (5.5 GB)    Partition A' (5.5 GB)        │
  │  [alice, bob, carol,   │  [dave, eve, frank, grace]  │
  │   heidi]               │                              │
  │                                                       │
  │  The hash space is split in half.                     │
  │  Future lookups route to the right partition          │
  │  based on the hash.                                   │
  └──────────────────────────────────────────────────────┘

  YOU DO NOTHING. DynamoDB detects the limit, splits, and rebalances.
  No downtime. No "reskinning" or rebalancing job you have to run.

  Contrast with Cassandra: YOU must add nodes, run nodetool cleanup,
  manage token ranges, plan vnode counts. DynamoDB does all of this
  invisibly.
```

### The Hot Partition Problem

```
Auto-splitting solves STORAGE and THROUGHPUT limits. But it can't
solve a HOT PARTITION caused by your key design.

  Bad schema:
    Table: Likes
    Partition key: "global"   ← EVERY like goes to ONE partition

    Like #1  → partition key "global" → hash → Partition X
    Like #2  → partition key "global" → hash → Partition X
    Like #3  → partition key "global" → hash → Partition X
    ...
    Like #1,000,000 → partition key "global" → Partition X

  Partition X is now on fire. DynamoDB can't help:
    → Splitting doesn't help (same key = same partition)
    → You're throttled (ProvisionedThroughputExceeded)
    → Latency spikes

  Fix: Use a high-cardinality partition key:
    Partition key: (post_id)  ← distributes likes across many partitions

  RULE: A partition key should have MANY DISTINCT VALUES, each
        accessed roughly equally. If one value gets >3000 RCU or
        >1000 WCU sustained, you have a hot partition.
```

---

<a id="data-model"></a>
## Data Model — Tables, Items, Keys

### The Hierarchy

```
AWS ACCOUNT
  └── REGION (e.g., us-east-1)
       └── TABLE (e.g., "Users")
            └── ITEM (one row, identified by its key)
                 └── ATTRIBUTE (a name-value pair, like a column)

  Example:
  Table: Users
  ┌──────────────────────────────────────────────────────────┐
  │  user_id (PK)  │ email            │ name   │ age  │ ...  │
  │────────────────│──────────────────│────────│──────│──────│
  │  "alice"       │ alice@x.com      │ Alice  │ 30   │      │
  │  "bob"         │ bob@y.com        │ Bob    │ 25   │      │
  │  "carol"       │ carol@z.com      │ Carol  │ 41   │      │
  └──────────────────────────────────────────────────────────┘

  Each ROW is an ITEM.
  Each COLUMN HEADER is an ATTRIBUTE NAME.
  Each CELL is an ATTRIBUTE VALUE.

  Unlike SQL: different items can have DIFFERENT attributes.
    → "alice" item might have a "phone" attribute
    → "bob" item might not have "phone" at all
    → This is "schemaless" (no fixed columns required)
```

### The Two Key Types

```
DynamoDB supports TWO primary key designs:

DESIGN 1: SIMPLE PRIMARY KEY (partition key only)
  ┌─────────────────────────────────────────────┐
  │  PK = user_id                                │
  │──────────────────────────────────────────────│
  │  user_id   │  name   │  email                │
  │  "alice"   │  Alice  │  alice@x.com          │
  │  "bob"     │  Bob    │  bob@y.com            │
  └─────────────────────────────────────────────┘

  → Each partition key value is UNIQUE (no duplicates)
  → One item per key
  → Use when you look up items by a single identifier

DESIGN 2: COMPOSITE PRIMARY KEY (partition key + sort key)
  ┌──────────────────────────────────────────────────────────┐
  │  PK = user_id        SK = created_at                      │
  │──────────────────────────────────────────────────────────│
  │  user_id  │ created_at          │ message                 │
  │  "alice"  │ 2024-07-26 10:00    │ "Hello"                 │
  │  "alice"  │ 2024-07-26 10:05    │ "World"                 │
  │  "alice"  │ 2024-07-26 10:10    │ "How are you?"          │
  │  "bob"    │ 2024-07-26 09:00    │ "Hi"                    │
  └──────────────────────────────────────────────────────────┘

  → The COMBINATION (PK + SK) must be unique
  → MANY items can share the same partition key
  → Within a partition, items are SORTED by the sort key
  → Use when you have "one to many" relationships

  This is the more powerful design — use it for almost everything.
```

### Why the Sort Key Is So Powerful

```
With a composite key, you can do range queries WITHIN a partition:

  Query: "Get all messages for alice after 10:00, newest first"

    Query(
      KeyConditionExpression="user_id = :u AND created_at > :t",
      ScanIndexForward=False,    # descending
      Limit=10
    )

  DynamoDB reads ONE partition (user_id="alice"), scans the B-tree
  in order, and returns the items. This is O(log n + result size).

  ┌──────────────────────────────────────────────┐
  │  PARTITION: user_id="alice"                  │
  │  (B-tree sorted by created_at)               │
  │                                              │
  │  10:00 ──► 10:05 ──► 10:10 ──► 10:15         │
  │   "Hello"  "World"  "How are"  "Goodbye"     │
  │                                              │
  │  Query with SK > 10:00, DESC, LIMIT 10:      │
  │  → Walk backward from newest. Return matches.│
  │  → Single partition read. <5 ms.             │
  └──────────────────────────────────────────────┘

  You can also use these operators on the sort key:
    =, <, <=, >, >=, BETWEEN, begins_with()

  begins_with() is HUGELY useful for hierarchical keys:
    SK = "status#active"      → begins_with("status#")
    SK = "2024-07#order#123"  → begins_with("2024-07#")
```

### Attribute Types

```
DynamoDB supports these attribute types:

  String (S):       "alice"
  Number (N):       42 (also 3.14 — DynamoDB doesn't distinguish int/float)
  Binary (B):       base64-encoded bytes (great for images, hashes)
  Boolean (BOOL):   true / false
  Null (NULL):      null (useful for sparse data)
  List (L):         [1, "two", true]  (ordered, mixed types)
  Map (M):          {"name": "Alice", "age": 30}  (nested document)
  String Set (SS):  {"red", "green", "blue"}  (unique strings)
  Number Set (NS):  {1, 2, 3}
  Binary Set (BS):  {b"a", b"b"}

  Note: Sets automatically dedupe. Lists and Maps don't.

  Example item with nested data:
  {
    "user_id":   {"S": "alice"},
    "profile":   {"M": {
                    "name": {"S": "Alice"},
                    "addresses": {"L": [
                      {"M": {"city": {"S": "NYC"}, "zip": {"S": "10001"}}},
                      {"M": {"city": {"S": "SF"},  "zip": {"S": "94101"}}}
                    ]}
                  }},
    "tags":      {"SS": ["vip", "newsletter"]}
  }

  Nested documents up to 32 levels deep.
  Max item size: 400 KB (this is a HARD limit — plan around it).
```

---

<a id="reads"></a>
## How Reads Work — Consistency Models

### Eventually Consistent vs Strongly Consistent Reads

```
When you read from DynamoDB, you have TWO choices:

EVENTUALLY CONSISTENT READ (default):
  → DynamoDB reads from ONE replica
  → Might return STALE data (if a recent write hasn't replicated yet)
  → Replication lag is usually < 1 second
  → CHEAPER: consumes HALF the read capacity

STRONGLY CONSISTENT READ:
  → DynamoDB reads from the LEADER replica (the one that accepted the write)
  → Always returns the LATEST written value
  → More expensive: consumes FULL read capacity
  → Slightly higher latency
  → Not available on Global Secondary Indexes (GSIs are always eventual)

  ┌──────────────────────────────────────────────────────┐
  │  WRITE to partition P (3 replicas)                   │
  │                                                       │
  │  Replica 1 (leader)  ◄── write ◄── client            │
  │       │ (replicates)                                  │
  │       ▼                                               │
  │  Replica 2          Replica 3                         │
  │  (eventually        (eventually                       │
  │   catches up)         catches up)                     │
  │                                                       │
  │  Eventually consistent read:                          │
  │    → Reads from Replica 2 OR 3 (might be stale)       │
  │                                                       │
  │  Strongly consistent read:                            │
  │    → Reads from Replica 1 (leader) — always current   │
  └──────────────────────────────────────────────────────┘

WHEN TO USE WHICH:
  Eventually consistent:
    → User profile (showing your own name — a 1-sec lag is fine)
    → Leaderboards, counts ("1,234 likes" — close enough)
    → Product catalog, recommendations
    → MOST reads in MOST apps

  Strongly consistent:
    → Right after a write, read-your-writes (banking balance)
    → Financial transactions
    → Inventory checks before purchase ("is this item in stock NOW?")
    → Anything where stale data = bug or money lost
```

### The Three Read Operations

```
1. GetItem — read a single item by exact key
     GetItem(user_id="alice", created_at="2024-07-26 10:00")
     → Returns one item (or "not found")
     → Fastest, cheapest read

2. Query — read multiple items in ONE partition
     Query(user_id="alice", created_at > "2024-07-26 00:00")
     → Returns all matching items, sorted by sort key
     → Supports filters, limits, pagination
     → THE primary way to read from DynamoDB

3. Scan — read EVERY item in the table (or partition)
     Scan(FilterExpression="age > 30")
     → Reads the ENTIRE table, then filters
     → SLOW and EXPENSIVE at scale
     → Use ONLY for small tables or one-off admin jobs
     → NEVER use Scan in a hot path
```

### Why Scan Is Dangerous

```
Scan reads every item, even if your filter matches 1% of them.

  Table: 100 million items
  Scan(FilterExpression="status = 'active'")  # 1% are active

  → Reads ALL 100M items (consumes read capacity for all)
  → Returns only 1M matching items
  → You PAY for 100M reads to get 1M results

  FilterExpression is applied AFTER the read, not before.
  It reduces network transfer, NOT read capacity consumed.

  Fix: design your schema so you can QUERY instead of scan.
       Use a GSI with "status" as a partition key.
```

---

<a id="capacity"></a>
## Capacity Modes — Provisioned vs On-Demand

### The Two Billing Models

```
DynamoDB charges you based on how much you READ and WRITE.
Two ways to pay:

PROVISIONED MODE (the original, default):
  → You specify: "I need 5000 RCU and 2000 WCU"
  → DynamoDB reserves that capacity for you
  → You pay for what you PROVISION, whether you use it or not
  → Cheaper IF your traffic is predictable
  → Can enable AUTO-SCALING (adjusts provisioned based on usage)

ON-DEMAND MODE (the easy option):
  → You specify nothing
  → DynamoDB scales instantly to whatever you throw at it
  → You pay PER REQUEST (per million reads / writes)
  → More expensive per request, but no idle capacity cost
  → Perfect for: unpredictable traffic, new apps, spiky workloads

  ┌──────────────────────────────────────────────────────┐
  │  PROVISIONED                                         │
  │  ┌─────────────────────────────────────────────┐     │
  │  │  Provisioned: 5000 RCU                       │     │
  │  │  ─────────────────────                       │     │
  │  │  Traffic:  ████░░░░░░░░░░░ (using 1500)      │     │
  │  │  You PAY for 5000 (wasted 3500)              │     │
  │  └─────────────────────────────────────────────┘     │
  │  Cost: low per unit, but pay for idle                │
  │                                                       │
  │  ON-DEMAND                                            │
  │  ┌─────────────────────────────────────────────┐     │
  │  │  No limit. Whatever you use, you use.        │     │
  │  │  Traffic:  ████░░░░░░░░░░░ (using 1500)      │     │
  │  │  You PAY for 1500                            │     │
  │  │  If traffic spikes to 50,000 → instantly OK  │     │
  │  └─────────────────────────────────────────────┘     │
  │  Cost: higher per unit, but no idle waste            │
  └──────────────────────────────────────────────────────┘
```

### Read Capacity Units (RCU) and Write Capacity Units (WCU)

```
These are the currency of DynamoDB provisioned mode.

1 WCU = One write per second, for an item up to 1 KB
        → Item is 2.5 KB? That's 3 WCUs (rounded up)
        → Item is 500 bytes? Still 1 WCU (rounded up)

1 RCU = One strongly consistent read per second, for an item up to 4 KB
        → Eventually consistent reads are HALF price (2 reads per RCU)
        → Item is 10 KB? That's 3 RCUs (10/4, rounded up) strongly,
          or 2 RCUs eventually consistent

  ┌──────────────────────────────────────────────────────────┐
  │  WORKED EXAMPLE                                          │
  │                                                          │
  │  App: Chat app.                                          │
  │  - 10,000 messages written per second                    │
  │  - Each message averages 800 bytes                        │
  │  - 50,000 reads per second (eventually consistent)        │
  │  - Each read returns one 800-byte item                    │
  │                                                          │
  │  WCU calculation:                                        │
  │    800 bytes → rounds up to 1 KB → 1 WCU per write       │
  │    10,000 writes × 1 WCU = 10,000 WCU                    │
  │                                                          │
  │  RCU calculation:                                        │
  │    800 bytes → fits in 4 KB → 1 RCU per read             │
  │    Eventually consistent → 2 reads per RCU               │
  │    50,000 reads ÷ 2 = 25,000 RCU                         │
  │                                                          │
  │  Total: 10,000 WCU + 25,000 RCU per second               │
  └──────────────────────────────────────────────────────────┘
```

### How Partition Capacity Limits Work

```
Each partition has a HARD limit:
  → 3000 RCU per second
  → 1000 WCU per second

If your table has 10,000 WCU provisioned:
  → DynamoDB allocates ceil(10000 / 1000) = 10 partitions
  → Each partition can handle 1000 WCU

  ┌──────────────────────────────────────────────────────┐
  │  Table provisioned: 10,000 WCU                       │
  │                                                       │
  │  Partition 1: 1000 WCU   Partition 2: 1000 WCU       │
  │  Partition 3: 1000 WCU   Partition 4: 1000 WCU       │
  │  ...                                                  │
  │  Partition 10: 1000 WCU                               │
  │                                                       │
  │  IF all writes hit Partition 3 (hot partition):       │
  │    → Partition 3 throttles at 1000 WCU                │
  │    → Other 9 partitions idle                          │
  │    → You get ProvisionedThroughputExceeded errors     │
  │    → Even though you "have 10,000 WCU"                │
  └──────────────────────────────────────────────────────┘

  This is why HOT PARTITIONS are the #1 DynamoDB performance killer.
  Your total provisioned capacity doesn't matter if one partition
  gets all the traffic.
```

### Which Mode Should You Pick?

```
ON-DEMAND when:
  ✓ New app (don't know traffic yet)
  ✓ Unpredictable / spiky traffic (flash sales, viral moments)
  ✓ Dev / test environments
  ✓ You'd rather pay 2-3x more than deal with capacity planning

PROVISIONED when:
  ✓ Traffic is predictable and steady
  ✓ High steady-state traffic (saves 2-3x vs on-demand)
  ✓ You can set up auto-scaling policies
  ✓ Production app with known usage patterns

RULE OF THUMB: Start on-demand. Once traffic stabilizes and you
  can predict it, switch to provisioned + auto-scaling to save money.
```

---

<a id="indexes"></a>
## Secondary Indexes — LSI vs GSI

DynamoDB gives you ONE primary key. But apps often need to query by OTHER attributes. Secondary indexes solve this.

### The Problem Indexes Solve

```
Table: Users
  PK = user_id
  Attributes: email, name, age, ...

You want: "Find the user with email = alice@x.com"

  GetItem needs the PK. But the PK is user_id, not email.
  → You'd have to Scan the whole table (SLOW, EXPENSIVE).

  Solution: Create a secondary index with email as the key.

  GSI: UsersByEmail
    PK = email
    Projects: user_id

  Now: Query(UsersByEmail, email="alice@x.com") → returns user_id
       → Then GetItem(Users, user_id=...) → full user data
       → Two fast lookups instead of one slow scan.
```

### Local Secondary Index (LSI)

```
An LSI lets you use a DIFFERENT SORT KEY, but the SAME partition key.

  Base table:
    PK = user_id,  SK = created_at

  LSI "ByLastModified":
    PK = user_id,  SK = last_modified   ← different sort key!

  Constraints:
    → MUST be created WHEN the table is created (can't add later)
    → MAX 5 LSIs per table
    → Same partition key as the base table
    → Sort key must be a scalar (String, Number, Binary)
    → Strongly consistent reads ARE supported (reads from base table)
    → Size: all items with same PK in an LSI must fit in 10 GB

  ┌──────────────────────────────────────────────────────┐
  │  WHEN TO USE LSI                                      │
  │                                                       │
  │  You have one entity (user_id) and want to query it   │
  │  by MULTIPLE sort orders:                             │
  │                                                       │
  │  - user_id + created_at    (base)                     │
  │  - user_id + last_modified (LSI 1)                    │
  │  - user_id + priority      (LSI 2)                    │
  │                                                       │
  │  All within the SAME partition. Same user_id.         │
  └──────────────────────────────────────────────────────┘
```

### Global Secondary Index (GSI)

```
A GSI lets you use a COMPLETELY DIFFERENT partition key AND sort key.

  Base table:
    PK = user_id,  SK = created_at

  GSI "ByEmail":
    PK = email,    SK = (none)

  GSI "ByCityByAge":
    PK = city,     SK = age

  Constraints:
    → Can be created ANY TIME (not just at table creation)
    → MAX 20 GSIs per table (default; can request more)
    → DIFFERENT partition key from the base table
    → STRONGLY CONSISTENT reads NOT supported (always eventual)
    → Has its OWN capacity (RCU/WCU in provisioned mode)
    → Can be SPARSE (only items that have the indexed attributes appear)

  ┌──────────────────────────────────────────────────────┐
  │  WHEN TO USE GSI                                      │
  │                                                       │
  │  You need to query by a DIFFERENT access pattern:     │
  │                                                       │
  │  Base:  user_id  → user's orders                      │
  │  GSI 1: email    → find user by email (login)         │
  │  GSI 2: status   → find all "pending" orders          │
  │  GSI 3: date     → orders by date (reports)           │
  └──────────────────────────────────────────────────────┘
```

### LSI vs GSI Comparison

```
┌──────────────────────┬──────────────────────┬──────────────────────┐
│ Feature              │ LSI                  │ GSI                  │
├──────────────────────┼──────────────────────┼──────────────────────┤
│ Partition key        │ SAME as base table   │ DIFFERENT            │
│ Sort key             │ Different            │ Different (optional) │
│ When created         │ At table creation    │ Any time             │
│ Max per table        │ 5                    │ 20 (default)         │
│ Strong consistency   │ YES                  │ NO (eventual only)   │
│ Capacity             │ Shares base table's  │ Has its OWN RCU/WCU  │
│ Size limit           │ 10 GB per partition  │ No hard limit        │
│ Cost                 │ Free (uses base)     │ Pay for index RCU/WCU│
│ Writes               │ Synchronous          │ Asynchronous         │
└──────────────────────┴──────────────────────┴──────────────────────┘

RULE:
  If you need the SAME partition, different sort → LSI
  If you need a DIFFERENT partition key          → GSI
  If unsure                                      → GSI (more flexible)
```

### Sparse Indexes — A Powerful Trick

```
A GSI only contains items that HAVE the indexed attribute.

  Base table:
  ┌──────────────────────────────────────────────────┐
  │ user_id │ email        │ is_admin                │
  │ "alice" │ alice@x.com │ (absent)                │
  │ "bob"   │ bob@y.com   │ true                    │
  │ "carol" │ carol@z.com │ (absent)                │
  │ "dave"  │ dave@w.com  │ true                    │
  └──────────────────────────────────────────────────┘

  GSI "Admins" (PK = is_admin):
  ┌──────────────────────┐
  │ is_admin │ user_id   │
  │ "true"   │ "bob"     │   ← ONLY items with is_admin appear!
  │ "true"   │ "dave"    │   ← alice and carol are absent
  └──────────────────────┘

  This is a SPARSE INDEX.
  → Tiny (only 2 of 4 items)
  → Cheap to query
  → Perfect for "find all admins", "find all pending orders", etc.

  TRICK: Set a special attribute ONLY on items you want indexed.
         Those items appear in the GSI; others don't.
         → Efficiently query subsets without scanning.
```

---

<a id="transactions"></a>
## Transactions — ACID Across Multiple Items

### The Problem Transactions Solve

```
Transfer $100 from Alice to Bob:

  Step 1: Read Alice's balance ($150)
  Step 2: Read Bob's balance ($50)
  Step 3: Write Alice's balance ($50)    ← deduct $100
  Step 4: Write Bob's balance ($150)     ← add $100

  WITHOUT TRANSACTIONS:
    → If step 3 succeeds but step 4 fails → Alice lost $100, Bob got nothing
    → Money disappeared. BAD.

  WITH TRANSACTIONS:
    → Both writes succeed atomically, or NEITHER does
    → If anything fails, everything rolls back
    → ACID guarantee across multiple items
```

### How DynamoDB Transactions Work

```
DynamoDB provides TransactWriteItems and TransactReadItems.

  TransactWriteItems:
    → Up to 100 items, up to 4 MB total
    → All writes SUCCEED TOGETHER or FAIL TOGETHER
    → ACID: Atomic, Consistent, Isolated, Durable

  TransactReadItems:
    → Up to 100 items
    → All reads see a CONSISTENT snapshot

  ┌──────────────────────────────────────────────────────┐
  │  TRANSACT WRITE FLOW                                 │
  │                                                       │
  │  Client: TransactWriteItems([                        │
  │    {ConditionCheck: alice.balance >= 100},            │
  │    {Update: alice.balance -= 100},                    │
  │    {Update: bob.balance += 100}                       │
  │  ])                                                   │
  │                                                       │
  │  DynamoDB:                                            │
  │    1. Acquires locks on BOTH items (alice, bob)       │
  │    2. Checks condition (alice has >= 100)             │
  │    3. If OK: applies BOTH writes atomically           │
  │    4. Releases locks                                  │
  │    5. Returns success                                 │
  │                                                       │
  │  If condition fails → TransactionCanceledException    │
  │  If any item is locked → TransactionInProgress        │
  └──────────────────────────────────────────────────────┘
```

### Cost of Transactions

```
Transactions are EXPENSIVE:
  → Each item in a transaction costs 2x the normal RCU/WCU
  → A 2-item write = 4x WCU (2 items × 2x multiplier)

  Why? Because transactions require:
    → Locking items (prevents concurrent writes)
    → Coordinating across partitions
    → A 2-phase-commit-like protocol

  RULE: Use transactions ONLY when you truly need ACID.
        Most apps can redesign to avoid them (single-item updates,
        atomic counters, or denormalization).
```

### Transaction Limitations

```
  ✗ Max 100 items per transaction
  ✗ Max 4 MB total
  ✗ Can't span multiple tables (all items in one table)
     → Wait, actually you CAN span tables, but each action is one item
  ✗ Can't use LSI/GSI in transactions
  ✗ Higher cost (2x capacity)
  ✗ If an item is involved in 2 concurrent transactions, one blocks

  Common patterns that AVOID transactions:
    → Atomic counter: UpdateItem with ADD expression (single item)
    → Conditional write: PutItem with ConditionExpression
    → Single-item updates are always atomic in DynamoDB
```

---

<a id="streams"></a>
## DynamoDB Streams — Change Data Capture

### What Streams Are

```
DynamoDB Streams is an ordered log of EVERY CHANGE to your table.

  Whenever you Create, Update, or Delete an item:
    → DynamoDB writes a record to the Stream
    → Record contains: old image, new image (or both)
    → Records are ordered PER PARTITION (not globally)
    → Records live for 24 hours, then expire

  ┌──────────────────────────────────────────────────────┐
  │  Table: Users                                        │
  │                                                       │
  │  PutItem(alice) ──► Stream record:                   │
  │    {eventName: INSERT, newImage: {alice's data}}      │
  │                                                       │
  │  UpdateItem(alice) ──► Stream record:                │
  │    {eventName: MODIFY, oldImage: {...}, newImage: {...}}│
  │                                                       │
  │  DeleteItem(alice) ──► Stream record:                │
  │    {eventName: REMOVE, oldImage: {alice's data}}      │
  └──────────────────────────────────────────────────────┘
```

### What You Can Configure

```
Stream view type (what each record contains):

  KEYS_ONLY:      Just the partition key + sort key
                  → Cheapest. Use when you only need to know WHAT changed.

  NEW_IMAGE:      The full item AFTER the change
                  → Use when downstream needs the new state.

  OLD_IMAGE:      The full item BEFORE the change
                  → Use for audit logs, undo features.

  NEW_AND_OLD_IMAGES: Both before and after
                  → Most info, most expensive. Use for full auditing.
```

### The Killer Use Case: Triggering Lambda

```
The #1 use of DynamoDB Streams is triggering AWS Lambda functions.

  ┌──────────────────────────────────────────────────────┐
  │  ARCHITECTURE                                         │
  │                                                       │
  │  App ──PutItem──► DynamoDB Table                     │
  │                       │                               │
  │                       ▼ (new record)                  │
  │                   DynamoDB Stream                     │
  │                       │                               │
  │                       ▼ (polls stream)                │
  │                   Lambda Function                    │
  │                       │                               │
  │                       ▼ (do something)                │
  │                   - Send email                        │
  │                   - Update search index (OpenSearch)  │
  │                   - Call another service              │
  │                   - Aggregate analytics               │
  │                   - Replicate to another table        │
  └──────────────────────────────────────────────────────┘

  Example: User signs up → DynamoDB write → Stream → Lambda
           → Lambda sends welcome email + adds to analytics

  You NEVER poll the stream yourself. Lambda does it for you:
    → Polls every ~1 second (batch window configurable)
    → Batches up to 1000 records or 6 MB
    → Invokes your function with the batch
    → Retries failed batches (configurable)
```

### Kinesis Data Streams Integration

```
DynamoDB can ALSO send changes to Kinesis Data Streams (KDS),
not just DynamoDB Streams.

  DynamoDB Stream:    Proprietary, 24-hour retention, Lambda-only consumer
  Kinesis Data Stream: Standard AWS streaming, 365-day retention,
                        multiple consumers (Lambda, Kinesis Analytics,
                        Firehose → S3, custom KCL consumers)

  ┌──────────────────────────────────────────────────────┐
  │  KDS INTEGRATION                                      │
  │                                                       │
  │  DynamoDB ──► Kinesis Data Stream ──► Consumer 1     │
  │                                  ──► Consumer 2      │
  │                                  ──► Firehose ──► S3 │
  │                                                       │
  │  Use when:                                            │
  │    → You need MULTIPLE consumers                      │
  │    → You need longer than 24h retention               │
  │    → You want to archive to S3 via Firehose           │
  │    → You want cross-account / cross-region streaming  │
  └──────────────────────────────────────────────────────┘
```

---

<a id="global-tables"></a>
## Global Tables — Multi-Region Replication

### The Problem

```
Your app has users in US, Europe, and Asia.

  Single-region DynamoDB (us-east-1):
    → US user: 20 ms latency  ✓
    → EU user: 120 ms latency ✗ (cross-Atlantic round trip)
    → Asia user: 250 ms latency ✗✗

  You want: a DynamoDB table in EACH region, all kept in sync,
  so users read from the NEAREST region.
```

### How Global Tables Work

```
Global Tables replicates your table across multiple AWS regions.

  ┌──────────────────────────────────────────────────────┐
  │  GLOBAL TABLE: "Users"                                │
  │                                                       │
  │  us-east-1 (Virginia)  ◄──────►  eu-west-1 (Ireland) │
  │       │                              │                │
  │       │                              │                │
  │       └──────────► ap-south-1 (Mumbai) ◄──────────────┘│
  │                                                       │
  │  - Writes to ANY region replicate to ALL others       │
  │  - Replication is ASYNCHRONOUS (typically < 1 sec)    │
  │  - Each region is a FULLY USABLE replica              │
  │  - Reads are LOCAL (fast)                             │
  │  - Writes are LOCAL (fast), then replicated           │
  └──────────────────────────────────────────────────────┘

  Setup:
    1. Create the table in region 1
    2. Enable Streams (required for replication)
    3. "Add region" → choose region 2, region 3
    4. DynamoDB handles the rest (creates replicas, sets up replication)

  No app changes needed. Your code talks to the regional endpoint.
  AWS SDK routes to the nearest region automatically (or you configure it).
```

### Conflict Resolution — Last Writer Wins

```
What if two regions update the SAME item at the SAME time?

  ┌──────────────────────────────────────────────────────┐
  │  CONFLICT                                             │
  │                                                       │
  │  us-east-1: UpdateItem(alice, name="Alice Smith")     │
  │              at T=10:00:00.000                        │
  │                                                       │
  │  eu-west-1: UpdateItem(alice, name="Alice Jones")     │
  │              at T=10:00:00.001 (1 ms later)           │
  │                                                       │
  │  Both writes succeed locally.                         │
  │  Both replicate to the other region.                  │
  │                                                       │
  │  RESOLUTION: Last Writer Wins                         │
  │    → DynamoDB compares timestamps                     │
  │    → The LATER write (eu-west-1, 10:00:00.001) wins   │
  │    → Final value everywhere: "Alice Jones"            │
  └──────────────────────────────────────────────────────┘

  Implications:
    → Global Tables is EVENTUALLY CONSISTENT across regions
    → You CANNOT get strong consistency across regions
    → If your app needs strict consistency, keep writes in ONE region
    → For conflict-prone data (counters), use atomic operations
      or accept that counts may be slightly off during concurrent writes
```

### Use Cases

```
  ✓ Globally distributed app with local reads (gaming, social)
  ✓ Disaster recovery (if us-east-1 goes down, eu-west-1 still works)
  ✓ Data residency (keep EU user data in EU region)
  ✓ Low-latency writes globally (each region accepts writes)

  ✗ Don't use if you need global strong consistency
  ✗ Don't use if you can't tolerate last-writer-wins conflicts
  ✗ Don't use for write-heavy counters that are updated globally
```

---

<a id="dax"></a>
## DAX — In-Memory Acceleration

### The Problem DAX Solves

```
DynamoDB reads are fast: single-digit milliseconds.
But for some workloads, that's not fast enough.

  Hot read pattern: millions of reads for the SAME item
    → "What's the score of the Super Bowl game?"
    → Millions of users hit DynamoDB for the same row
    → You pay for every read (expensive at scale)

  DAX (DynamoDB Accelerator) is a fully managed, in-memory cache
  that sits in front of DynamoDB.

  ┌──────────────────────────────────────────────────────┐
  │  WITH DAX                                             │
  │                                                       │
  │  App ──► DAX Cluster ──► DynamoDB                    │
  │           (in-memory)                                  │
  │                                                       │
  │  Read:                                                │
  │    1. Check DAX cache (microseconds)                  │
  │    2. Cache hit? Return immediately (NO DynamoDB read)│
  │    3. Cache miss? Read from DynamoDB, cache it        │
  │                                                       │
  │  Write:                                               │
  │    1. Write to DAX                                    │
  │    2. DAX writes through to DynamoDB                  │
  │    3. Cache updated                                   │
  └──────────────────────────────────────────────────────┘
```

### DAX vs Redis (DIY Caching)

```
┌────────────────────┬────────────────────────┬───────────────────────┐
│ Feature            │ DAX                    │ Redis (DIY)           │
├────────────────────┼────────────────────────┼───────────────────────┤
│ Setup              │ Fully managed          │ You run it            │
│ DynamoDB API       │ IDENTICAL (drop-in)    │ Different API         │
│ Cache invalidation │ Automatic (write-through)│ Manual (your code)  │
│ TTL support        │ Yes                    │ Yes                   │
│ Latency            │ Microseconds           │ Microseconds          │
│ Cost               │ Pay per node hour      │ Pay per node hour     │
│ Best for           │ Pure DynamoDB caching  │ Multi-purpose caching │
└────────────────────┴────────────────────────┴───────────────────────┘

  KEY ADVANTAGE of DAX: Your app code barely changes.
    → boto3.client('dynamodb') → boto3.client('dax')
    → Same API, same methods, but now cached.

  KEY LIMITATION of DAX:
    → Eventually consistent reads ONLY (can't cache strong reads)
    → Only for DynamoDB (not general-purpose)
    → Adds cost (you pay for DAX nodes + DynamoDB)
```

---

<a id="ttl"></a>
## TTL — Auto-Expiring Items

### What TTL Does

```
TTL lets you set an expiration time on items. After that time,
DynamoDB deletes them automatically.

  PutItem:
    user_id = "session_123"
    expires_at = 1690000000   ← Unix timestamp (seconds)

  Set the table's TTL attribute to "expires_at".

  After expires_at passes:
    → DynamoDB deletes the item (within ~48 hours — not instant!)
    → Deletion generates a Stream record (REMOVE event)
    → You don't pay for the deletion

  ┌──────────────────────────────────────────────────────┐
  │  TTL TIMELINE                                         │
  │                                                       │
  │  T=0:        Write item, expires_at = T + 3600        │
  │  T=3600:     Item is "expired" (logically)            │
  │  T=3600+ε:   Item might still be readable for a while │
  │  T=3600+~48h:Item is PHYSICALLY deleted by background │
  │              process. Stream record emitted.          │
  │                                                       │
  │  IMPORTANT: TTL deletion is NOT immediate.             │
  │  It can take up to 48 hours after expiry.             │
  │  Your app should still handle "expired" items (filter │
  │  by expires_at < now in your query).                  │
  └──────────────────────────────────────────────────────┘
```

### Common Use Cases

```
  ✓ Session tokens (expire after 1 hour of inactivity)
  ✓ OTP codes (expire after 5 minutes)
  ✓ Event logs (auto-delete after 30 days)
  ✓ Cart items (abandoned carts expire)
  ✓ Rate limit counters (per-minute windows)

  WHY IT'S GREAT:
    → No cleanup jobs to run
    → No Scan + Delete (which costs read + write capacity)
    → Deletions are FREE (don't consume WCU)
    → Stream record lets you react (e.g., log the deletion)
```

---

<a id="backups"></a>
## Backups — PITR and On-Demand

### Point-In-Time Recovery (PITR)

```
PITR continuously backs up your table for the last 35 days.

  Enable it once → DynamoDB handles the rest.

  ┌──────────────────────────────────────────────────────┐
  │  PITR                                                 │
  │                                                       │
  │  Now ◄──────── 35 days ──────── Past                  │
  │  │                                                    │
  │  │ You can restore to ANY second in this window       │
  │  │                                                    │
  │  "Restore table to July 25, 3:47:22 PM"               │
  │  → DynamoDB creates a NEW table with data as of then  │
  │  → Original table is untouched                        │
  │  → Restore takes minutes to hours (depends on size)   │
  └──────────────────────────────────────────────────────┘

  Cost: based on table size (continuous backup storage)
  Use case: accidental deletion, bad deploy corrupted data,
            ransomware, "oops I dropped the wrong item"
```

### On-Demand Backups

```
You can take a full snapshot at any time:

  aws dynamodb create-backup \
    --table-name Users \
    --backup-name "pre-migration-2024-07-26"

  → Creates a complete backup of the table (data + configuration)
  → Retained until YOU delete it (no expiry)
  → Restore creates a new table from the backup

  Use case: before migrations, before big schema changes,
            compliance / archival, disaster recovery snapshots.

  NOTE: Backups don't consume read capacity (they're offline copies).
        Restores create a NEW table (don't overwrite the original).
```

### What Backups Do NOT Include

```
  ✗ GSI data is NOT backed up (GSIs are rebuilt on restore)
  ✗ Stream records are NOT backed up
  ✗ Auto-scaling settings (must reconfigure after restore)
  ✗ The restore is to a NEW table (you must repoint your app)

  Best practice: TEST your restore process regularly.
    A backup you've never restored is a hope, not a backup.
```

---

<a id="real-apps"></a>
## How Real Companies Use DynamoDB

| Company | Use Case | Scale |
|---------|----------|-------|
| **Amazon** | Shopping cart, order history, session state | Prime Day 2023: 126M requests/sec |
| **Airbnb** | Booking metadata, message threading | 150M+ users, millions of bookings |
| **Supercell** | Game state (Clash of Clans player data) | 100M+ daily players |
| **Netflix** | User session state, device management | 260M+ users |
| **Snap** | User preferences, story metadata | 750M+ users |
| **Duolingo** | Lesson progress, streaks, user state | 500M+ users |
| **Coinbase** | Account state, transaction logs | 100M+ users |

### Amazon — Shopping Cart

```
Amazon's shopping cart runs on DynamoDB.

  Table: Cart
    PK = user_id (or session_id for guests)
    SK = item_id
    Attributes: quantity, added_at, price_snapshot

  ┌──────────────────────────────────────────────────┐
  │  user_id    │ item_id  │ quantity │ added_at      │
  │ "user_123"  │ "B00123" │ 2        │ 2024-07-26... │
  │ "user_123"  │ "B00456" │ 1        │ 2024-07-26... │
  └──────────────────────────────────────────────────┘

  Why DynamoDB:
    → Cart access is a simple key lookup (GetItem / Query)
    → Cart state changes constantly (add, remove, update quantity)
    → TTL can auto-expire abandoned carts (after 30 days)
    → Scales to handle Prime Day's 126M requests/sec
    → No ops team needed to keep it alive

  During Prime Day, Amazon's DynamoDB traffic is the largest
  NoSQL workload on Earth. The shopping cart alone handles
  tens of millions of concurrent users.
```

### Airbnb — Bookings

```
Airbnb stores booking metadata in DynamoDB.

  Table: Reservations
    PK = listing_id (the property)
    SK = check_in_date

  ┌──────────────────────────────────────────────────────┐
  │  listing_id │ check_in_date │ guest_id │ status       │
  │ "listing_1" │ 2024-08-01    │ "g_100"  │ confirmed    │
  │ "listing_1" │ 2024-08-08    │ "g_101"  │ pending      │
  │ "listing_2" │ 2024-08-01    │ "g_102"  │ confirmed    │
  └──────────────────────────────────────────────────────┘

  Query: "Is listing_1 available Aug 1-7?"
    → Query(listing_id="listing_1", check_in_date BETWEEN Aug1 AND Aug7)
    → Single partition read. Instant.

  GSI: ReservationsByGuest
    PK = guest_id, SK = check_in_date
    → "Show me all my trips" → Query the GSI

  Why DynamoDB:
    → Booking lookups are key-based (not ad-hoc queries)
    → Massive read load (users browsing thousands of listings)
    → Global Tables for multi-region low latency
    → Streams → Lambda → update search index, send notifications
```

### Supercell — Game State

```
Supercell (Clash of Clans, Hay Day, Brawl Stars) stores player
state in DynamoDB.

  Table: PlayerState
    PK = player_id
    Attributes: gold, gems, troops, level, village_layout, ...

  ┌──────────────────────────────────────────────────────┐
  │  player_id │ gold   │ gems  │ level │ troops          │
  │ "p_1001"   │ 125000 │ 340   │ 42    │ {archers: 50}  │
  │ "p_1002"   │ 89000  │ 12    │ 28    │ {giants: 20}   │
  └──────────────────────────────────────────────────────┘

  Why DynamoDB:
    → Every player action = read + write (gold changes, troops move)
    → 100M+ daily players = massive read/write throughput
    → Single-digit ms latency = responsive gameplay
    → On-demand mode handles traffic spikes (new game launch, events)
    → Global Tables for players in every continent

  Challenge: game state changes constantly.
    → A player raids a village → gold increases
    → Atomic counter: UpdateItem(ADD gold :amount)
    → Single-item atomic update, no transaction needed.
```

---

<a id="build"></a>
## How YOU Can Build With It

### AWS Setup

```bash
# 1. Install AWS CLI
pip install awscli

# 2. Configure credentials (get these from AWS IAM console)
aws configure
#   AWS Access Key ID: AKIA...
#   AWS Secret Access Key: ...
#   Default region: us-east-1

# 3. Install Boto3 (Python SDK)
pip install boto3
```

### Create a Table

```bash
# Create a table with composite key
aws dynamodb create-table \
    --table-name Messages \
    --attribute-definitions \
        AttributeName=user_id,AttributeType=S \
        AttributeName=sent_at,AttributeType=N \
    --key-schema \
        AttributeName=user_id,KeyType=HASH \
        AttributeName=sent_at,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST

# "HASH" = partition key, "RANGE" = sort key
# PAY_PER_REQUEST = on-demand mode (no capacity planning)
```

### Python SDK Examples

```python
import boto3
from boto3.dynamodb.conditions import Key, Attr
from decimal import Decimal

# Connect to the table
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('Messages')

# ─────────────────────────────────────────────────────
# PUT ITEM (create or overwrite)
# ─────────────────────────────────────────────────────
table.put_item(
    Item={
        'user_id': 'alice',
        'sent_at': 1722000000,        # Unix timestamp
        'sender': 'alice',
        'text': 'Hello World!',
        'read': False
    }
)

# ─────────────────────────────────────────────────────
# GET ITEM (single item by exact key)
# ─────────────────────────────────────────────────────
response = table.get_item(
    Key={'user_id': 'alice', 'sent_at': 1722000000},
    ConsistentRead=True       # strongly consistent (optional)
)
item = response.get('Item')
print(item)  # {'user_id': 'alice', 'text': 'Hello World!', ...}

# ─────────────────────────────────────────────────────
# QUERY (multiple items in one partition, with sort key)
# ─────────────────────────────────────────────────────
response = table.query(
    KeyConditionExpression=Key('user_id').eq('alice') & 
                           Key('sent_at').gt(1721900000),
    ScanIndexForward=False,    # newest first (descending)
    Limit=10
)
for item in response['Items']:
    print(item['text'])

# ─────────────────────────────────────────────────────
# UPDATE ITEM (with conditional expression)
# ─────────────────────────────────────────────────────
table.update_item(
    Key={'user_id': 'alice', 'sent_at': 1722000000},
    UpdateExpression='SET #r = :true',
    ConditionExpression='attribute_exists(user_id)',  # only if exists
    ExpressionAttributeNames={'#r': 'read'},
    ExpressionAttributeValues={':true': True}
)

# ─────────────────────────────────────────────────────
# ATOMIC COUNTER (increment a number safely)
# ─────────────────────────────────────────────────────
response = table.update_item(
    Key={'user_id': 'alice', 'sent_at': 1722000000},
    UpdateExpression='ADD likes :one',
    ExpressionAttributeValues={':one': Decimal(1)},
    ReturnValues='UPDATED_NEW'
)
print(response['Attributes']['likes'])  # the new value

# ─────────────────────────────────────────────────────
# TRANSACTION (multiple items, all-or-nothing)
# ─────────────────────────────────────────────────────
dynamodb_client = boto3.client('dynamodb', region_name='us-east-1')
dynamodb_client.transact_write_items(
    TransactItems=[
        {
            'Update': {
                'TableName': 'Accounts',
                'Key': {'user_id': {'S': 'alice'}},
                'UpdateExpression': 'SET balance = balance - :amt',
                'ConditionExpression': 'balance >= :amt',
                'ExpressionAttributeValues': {
                    ':amt': {'N': '100'}
                }
            }
        },
        {
            'Update': {
                'TableName': 'Accounts',
                'Key': {'user_id': {'S': 'bob'}},
                'UpdateExpression': 'SET balance = balance + :amt',
                'ExpressionAttributeValues': {
                    ':amt': {'N': '100'}
                }
            }
        }
    ]
)
```

### Schema Design — The Most Important Skill

```
DynamoDB schema design is INVERTED from SQL schema design.

  SQL:  Model your DATA → then figure out queries later.
  DynamoDB: Model your QUERIES → then design tables to answer them.

STEP 1: List every query your app needs.
  "Get user by user_id"
  "Get user by email"
  "Get all orders for a user"
  "Get orders by status (pending)"
  "Get orders by date range"

STEP 2: For each query, identify the access pattern.
  → What's the partition key? (the "filter")
  → What's the sort key? (the "ordering")

STEP 3: Design ONE table per entity (or use single-table design).

  Example: single-table design for an e-commerce app

  ┌──────────────────────────────────────────────────────────┐
  │  TABLE: Ecommerce                                        │
  │  PK = USER#alice     SK = PROFILE           → user data   │
  │  PK = USER#alice     SK = ORDER#1001        → order       │
  │  PK = USER#alice     SK = ORDER#1002        → order       │
  │  PK = ORDER#1001     SK = ITEM#sku123       → line item   │
  │                                                          │
  │  GSI 1 (ByEmail):   PK = email       SK = USER#alice     │
  │  GSI 2 (ByStatus):  PK = STATUS#pending  SK = ORDER#1001 │
  └──────────────────────────────────────────────────────────┘

  Single-table design puts multiple entity types in ONE table.
  → Fewer tables to manage
  → Efficient retrievals (one Query gets related data)
  → But harder to understand (steep learning curve)

  START SIMPLE: one table per entity. Move to single-table only
  when you're comfortable and have a clear access pattern list.
```

### Build a Real App: Simple Chat

```python
"""
A minimal chat backend using DynamoDB.
Stores messages, retrieves recent messages per conversation.
"""

import boto3
import time

table = boto3.resource('dynamodb', region_name='us-east-1').Table('Chat')

def send_message(conversation_id, sender, text):
    """Store a message. conversation_id is the partition key."""
    table.put_item(Item={
        'conversation_id': conversation_id,
        'sent_at': int(time.time() * 1000),   # millisecond timestamp
        'sender': sender,
        'text': text,
    })

def get_recent_messages(conversation_id, limit=50):
    """Get the last N messages for a conversation."""
    response = table.query(
        KeyConditionExpression=Key('conversation_id').eq(conversation_id),
        ScanIndexForward=False,   # newest first
        Limit=limit
    )
    return list(reversed(response['Items']))   # return oldest-to-newest

# Usage
send_message('conv_1', 'alice', 'Hi Bob!')
send_message('conv_1', 'bob',   'Hey Alice!')
messages = get_recent_messages('conv_1')
for m in messages:
    print(f"{m['sender']}: {m['text']}")
```

---

<a id="interview"></a>
## Common Interview Questions

**Q: How does DynamoDB partition data, and what's a hot partition?**

A: DynamoDB hashes the partition key to determine which storage partition holds the item. The hash maps the key to a position in a fixed partition space, and all items with the same partition key land on the same partition. Each partition holds up to 10 GB and handles up to 3000 RCU / 1000 WCU. A hot partition occurs when one partition key value receives disproportionate traffic — e.g., all "likes" use partition key "global." Since all that traffic hits one partition, you get throttled even if your total provisioned capacity is high. The fix is to choose a high-cardinality partition key that distributes traffic evenly across many partitions.

**Q: Explain the difference between eventually consistent and strongly consistent reads.**

A: DynamoDB replicates each partition across 3 Availability Zones. An eventually consistent read (the default) reads from any replica, which may be stale if a recent write hasn't replicated yet — but it costs half the read capacity. A strongly consistent read reads from the leader replica (the one that accepted the write), guaranteeing the latest value, but costs full read capacity and isn't available on Global Secondary Indexes. Use eventually consistent for most reads (profiles, counts, feeds) and strongly consistent when you must read-your-writes (banking, inventory checks right after a purchase).

**Q: What's the difference between an LSI and a GSI?**

A: A Local Secondary Index (LSI) uses the SAME partition key as the base table but a DIFFERENT sort key — useful when you want to query one entity (same partition key) in multiple sort orders. It must be created at table creation time, max 5 per table, supports strongly consistent reads, and shares the base table's capacity. A Global Secondary Index (GSI) uses a COMPLETELY DIFFERENT partition key — useful for entirely new access patterns like "find user by email" or "find all pending orders." GSIs can be created any time, max 20 per table by default, only support eventually consistent reads, and have their own RCU/WCU allocation. When in doubt, use a GSI — it's more flexible.

**Q: How do DynamoDB transactions work, and when should you avoid them?**

A: `TransactWriteItems` applies up to 100 item writes atomically — all succeed or all fail. DynamoDB acquires locks on the involved items, performs a 2-phase-commit-like protocol across partitions, and releases locks on completion. The cost is 2x the normal RCU/WCU per item, because of the locking and coordination overhead. You should avoid transactions when possible: most problems can be solved with single-item atomic updates (which are always free of cost beyond the normal write), conditional writes (ConditionExpression), or atomic counters (ADD expression). Use transactions only for true multi-item ACID needs like fund transfers or multi-entity state changes where partial failure would corrupt data.

**Q: How would you design a DynamoDB schema for a Twitter-like feed?**

A: Start by listing access patterns: (1) get a user's tweets, (2) get a user's home timeline (tweets from people they follow), (3) get tweets by hashtag. For (1), use a Tweets table with PK = user_id and SK = timestamp — a single partition query returns recent tweets sorted by time. For (2), you have two options: fan-out-on-write (when a user tweets, write to all followers' timeline partitions) or fan-out-on-read (query each followed user and merge). Fan-out-on-write is the classic approach — a Timeline table with PK = follower_id and SK = tweet_id, populated by a Lambda triggered by a Stream when a tweet is written. For (3), a GSI with PK = hashtag and SK = timestamp. The key insight: DynamoDB schema design is driven by access patterns, not by normalizing data.

**Q: What is DynamoDB Streams and how does it differ from Kinesis?**

A: DynamoDB Streams is an ordered log of every change (insert, modify, remove) to a table, retained for 24 hours, with records ordered per partition. Its primary use is triggering Lambda functions for change-data-capture workflows like sending notifications, updating search indexes, or replicating data. DynamoDB can also publish to Kinesis Data Streams, which offers longer retention (up to 365 days), multiple consumers, and integration with Firehose (to archive to S3) and Kinesis Analytics. Use DynamoDB Streams for simple single-consumer Lambda triggers; use Kinesis when you need multiple consumers, longer retention, or streaming analytics.

**Q: How do Global Tables handle conflicts?**

A: Global Tables uses last-writer-wins conflict resolution based on server-side timestamps. If two regions update the same item concurrently, DynamoDB compares the timestamps of the writes and the later one prevails across all regions. This means Global Tables is eventually consistent across regions — you cannot get strong cross-region consistency. For conflict-prone data like global counters, concurrent writes from different regions can lose updates. The mitigation is to route writes for a given item to a single region, use atomic operations (ADD), or accept approximate values for non-critical counts.

**Q: When would you use DAX instead of Redis?**

A: Use DAX when your caching needs are purely DynamoDB and you want a drop-in solution — your application code barely changes (just swap the client endpoint), cache invalidation is automatic via write-through, and there's no infrastructure to manage. Use Redis when you need general-purpose caching across multiple data sources, complex data structures (sorted sets, pub/sub), or caching logic that DAX can't express. DAX is simpler but narrower; Redis is more powerful but requires you to write and maintain the caching logic yourself.

**Q: What happens when a partition exceeds 10 GB or its throughput limit?**

A: DynamoDB automatically splits the partition. For storage, when a partition approaches 10 GB, DynamoDB splits it into two partitions, distributing the hash space evenly. For throughput, in provisioned mode, if a partition consistently approaches its 3000 RCU / 1000 WCU limit, DynamoDB splits it to spread load. In on-demand mode, DynamoDB instantly scales capacity to match traffic without pre-splitting. All of this happens transparently — you never see it, never configure it, and never experience downtime. The one thing auto-splitting CANNOT fix is a hot partition caused by a low-cardinality partition key, because all traffic for that key always lands on one partition regardless of splits.

---

> DynamoDB's promise is simple: make an API call, and your data is stored, replicated, backed up, and scaled to whatever traffic you generate — with no servers to manage. The trade-off is that you must design your schema around your queries, because DynamoDB will not bend its key-based access model to fit an ad-hoc query. Get the schema right, and DynamoDB will handle the rest, from a thousand users to a hundred million.

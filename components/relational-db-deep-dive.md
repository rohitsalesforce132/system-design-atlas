# MySQL & PostgreSQL — The Complete Deep Dive

> MySQL is mentioned 180 times and PostgreSQL 99 times across this atlas. Together they power Facebook, Instagram, Flipkart, Twitter, Uber, Spotify, Razorpay, and Zomato. This guide covers how they actually work inside — from B-tree indexes to MVCC to sharding at billion-user scale.

---

## Table of Contents

1. [How a Relational Database Works](#how-rdbms-works)
2. [Storage Engines — InnoDB (MySQL) vs Heap (PostgreSQL)](#storage)
3. [B-Tree Indexes — How They Actually Work](#btree)
4. [MVCC — How Concurrent Transactions Don't Block Each Other](#mvcc)
5. [The Write-Ahead Log (WAL)](#wal)
6. [Query Execution — Parser → Planner → Executor](#query)
7. [MySQL vs PostgreSQL — Real Differences](#mysql-vs-pg)
8. [Replication](#replication)
9. [Sharding at Facebook/Flipkart Scale](#sharding)
10. [Vitess — How YouTube Scales MySQL](#vitess)
11. [Performance Tuning](#tuning)
12. [How Real Companies Use Them](#real-apps)
13. [How YOU Can Build This](#build)

---

<a id="how-rdbms-works"></a>
## How a Relational Database Works

### The Core Architecture

```
┌───────────────────────────────────────────────────────┐
│                 RELATIONAL DATABASE                    │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Connection│  │  Parser   │  │  Query Planner/   │   │
│  │  Pool     │  │           │  │  Optimizer        │   │
│  │           │  │ SQL text  │  │                   │   │
│  │ Manages   │  │ → AST     │  │ Picks fastest     │   │
│  │ client    │  │ (parse    │  │ execution plan    │   │
│  │ connectns │  │  tree)    │  │                   │   │
│  └──────────┘  └──────────┘  └────────┬───────────┘   │
│                                        │               │
│                               ┌────────▼───────────┐   │
│                               │    Executor          │   │
│                               │                      │   │
│                               │  Runs the plan:      │   │
│                               │  - Scan table        │   │
│                               │  - Use index         │   │
│                               │  - Join tables       │   │
│                               │  - Aggregate         │   │
│                               │  - Sort              │   │
│                               └────────┬───────────┘   │
│                                        │               │
│  ┌────────────────────────────────────▼──────────┐    │
│  │              BUFFER POOL (RAM)                   │    │
│  │                                                   │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │    │
│  │  │ Page 1   │ │ Page 2   │ │ Page 3   │           │    │
│  │  │(table   │ │(index   │ │(table   │            │    │
│  │  │ data)   │ │ data)   │ │ data)   │            │    │
│  │  └─────────┘ └─────────┘ └─────────┘           │    │
│  │                                                   │    │
│  │  Recently accessed pages stay in RAM              │    │
│  │  (avoids disk reads)                              │    │
│  └───────────────────┬───────────────────────────┘    │
│                       │                               │
│                       │ (page not in buffer pool?      │
│                       │  read from disk)               │
│                       ▼                               │
│  ┌────────────────────────────────────────────────┐  │
│  │              DISK (SSD/HDD)                      │  │
│  │                                                   │  │
│  │  ┌──────────────┐  ┌──────────────┐            │  │
│  │  │ Data Files    │  │ WAL / Redo    │            │  │
│  │  │ (.ibd / .pg)  │  │ Log           │            │  │
│  │  └──────────────┘  └──────────────┘            │  │
│  └────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
```

### What Happens When You Run a Query

```
You type: SELECT * FROM users WHERE email = 'alice@email.com'

Step 1: PARSER
  → SQL text → Abstract Syntax Tree (AST)
  → Validates syntax, table/column names exist
  → "SELECT * FROM users WHERE email = 'x'" → valid AST

Step 2: PLANNER/OPTIMIZER (the brain)
  → Looks at AST, considers execution strategies:
     A) Full table scan: Read all 10M rows, filter by email
     B) Index scan: Use idx_users_email, find row in ~3 disk reads
  → Estimates cost of each (based on table statistics)
  → Picks the cheapest plan (B: index scan)
  → Output: Execution plan tree

Step 3: EXECUTOR
  → Follows the plan:
     a) Open idx_users_email (B-tree)
     b) Search for 'alice@email.com' in B-tree
     c) Find leaf node → contains row pointer (page 42, slot 7)
     d) Read page 42 from buffer pool (or disk)
     e) Return row

Step 4: RETURN RESULTS to client
```

---

<a id="storage"></a>
## Storage Engines — InnoDB vs PostgreSQL Heap

### MySQL / InnoDB

```
InnoDB is MySQL's default storage engine. It's a clustered index.

  WHAT IS A CLUSTERED INDEX?
  → The table data IS the index. They're the same B-tree.

  ┌─────────────────────────────────────────────────────┐
  │  InnoDB: Clustered Index (table = B-tree)            │
  │                                                      │
  │  PRIMARY KEY defines the order of data on disk.      │
  │                                                      │
  │           [Page: Internal Node]                      │
  │          ╱  PK < 100  │ PK ≥ 100  ╲                  │
  │         ╱              │             ╲                │
  │  [Leaf Page]      [Leaf Page]     [Leaf Page]       │
  │  PK=1, row data  PK=100, row     PK=500, row        │
  │  PK=2, row data  PK=101, row     PK=501, row        │
  │  PK=3, row data  PK=102, row     PK=502, row        │
  │  ...             ...            ...                  │
  │                                                      │
  │  Rows are PHYSICALLY ordered by PRIMARY KEY.          │
  │  → SELECT * WHERE id = 42 → direct B-tree lookup     │
  │  → INSERT with auto-increment → append to last page   │
  │  → INSERT with random PK → page splits (slow!)        │
  │                                                      │
  │  Secondary indexes store PRIMARY KEY, not row pointer │
  │  → Index lookup → get PK → cluster lookup → row data  │
  └─────────────────────────────────────────────────────┘

  IMPLICATIONS:
  → Range scans on PK are very fast (sequential pages)
  → Secondary index lookups require TWO B-tree traversals
  → Table can have only ONE clustered index (the PK)
  → Choose PK wisely: auto-increment INT/BIGINT is best
  → UUID PK: random inserts → page splits → fragmentation → slow
```

### PostgreSQL Heap

```
PostgreSQL uses a heap (unordered) + separate indexes.

  ┌──────────────────────────────────────────────────────┐
  │  PostgreSQL: Heap Table + Separate Indexes             │
  │                                                        │
  │  ┌──────────────────┐    ┌──────────────────────┐    │
  │  │  HEAP (data)      │    │  INDEX (B-tree)       │    │
  │  │                   │    │                        │    │
  │  │  Row 1: [data]    │◄───│  email='alice' → CTID  │    │
  │  │  Row 2: [data]    │    │  email='bob'   → CTID  │    │
  │  │  Row 3: [data]    │◄───│  email='carol' → CTID  │    │
  │  │  ...              │    │                        │    │
  │  │                   │    │  CTID = (page, slot)   │    │
  │  │  NOT ordered by   │    │  Direct pointer to     │    │
  │  │  any key.         │    │  heap row.             │    │
  │  │  Insert order.    │    │                        │    │
  │  └──────────────────┘    └──────────────────────┘    │
  │                                                        │
  │  IMPLICATIONS:                                         │
  │  → Any index is equally fast (no primary/secondary)    │
  │  → Index points directly to row (one lookup)           │
  │  → UUID inserts don't cause page splits on heap         │
  │  → But heap requires VACUUM to reclaim dead tuples      │
  │  → Multiple indexes = multiple separate B-trees         │
  └──────────────────────────────────────────────────────┘
```

---

<a id="btree"></a>
## B-Tree Indexes — How They Actually Work

### What Is a B-Tree?

```
B-Tree = Balanced Tree (NOT Binary Tree)

  Why balanced? Every leaf node is at the SAME depth.
  → Worst case lookup = tree height
  → For 1 billion rows: height ≈ 3-4
  → Maximum 3-4 disk reads to find ANY row

  ┌─────────────────────────────────────────────────┐
  │                  ROOT NODE                       │
  │            [50 | 100 | 200]                     │
  │           ╱    |       |      ╲                 │
  │     <50    50-100  100-200   >200               │
  │     ╱          ╱         ╲          ╲           │
  │  [10|20|30] [60|70|80] [120|150] [250|300|400] │
  │  LEAF      LEAF       LEAF      LEAF            │
  │  NODES     NODES      NODES     NODES           │
  │                                                  │
  │  Each internal node has keys that divide ranges  │
  │  Each leaf node has the actual data (or pointer) │
  │  Leaves are linked (→ next leaf) for range scans │
  └─────────────────────────────────────────────────┘
```

### B-Tree Lookup Example

```
Search for key=150 in the tree above:

  Step 1: Read ROOT node [50 | 100 | 200]
    → 150 > 100 and 150 < 200 → go to child "100-200"

  Step 2: Read node [120 | 150]
    → 150 = 150 → FOUND!

  Result: 2 disk reads (root + leaf) + 1 page already cached

  For 1 billion rows:
    Tree height = log(1,000,000,000) / log(200) ≈ 4
    → Maximum 4 disk reads to find any row
    → With buffer pool caching: often 0-1 disk reads
```

### B+ Tree (What Databases Actually Use)

```
Databases use B+ Trees (a variant):

  DIFFERENCE from B-Tree:
    → Internal nodes ONLY contain keys (no data)
    → ALL data is in leaf nodes
    → Leaf nodes are linked (doubly-linked list)

  WHY B+ Tree?
    → Internal nodes hold more keys → shorter tree → fewer reads
    → Range queries: find start leaf → walk sideways → very fast
    → SELECT * WHERE id BETWEEN 100 AND 500:
       → Find leaf with id=100
       → Walk right through linked leaves until id=500
       → Sequential reads (fast)

  ┌───────────────────────────────────────────┐
  │           [50 | 100 | 200]                 │  (internal: keys only)
  │          ╱       |       |      ╲          │
  │     [10|20|40]→[60|80]→[120|150]→[250|300]│  (leaves: data + links)
  │     ← linked →     ← linked →             │
  └───────────────────────────────────────────┘
```

### When Indexes DON'T Help

```
BAD: Low cardinality column
  CREATE INDEX idx_gender ON users (gender);
  → gender has 2 values (M/F)
  → Index returns 50% of rows for either value
  → DB does full table scan instead (faster than 50% index lookups)
  → Rule: Don't index columns with few unique values

BAD: Leading wildcard
  SELECT * FROM products WHERE name LIKE '%shoes';
  → B-tree can't help (search starts with %)
  → Full table scan

  FIX: Use full-text search (Elasticsearch) for text matching

BAD: Function on indexed column
  SELECT * FROM users WHERE LOWER(email) = 'alice@email.com';
  → Function breaks index usage
  → DB must scan all rows, apply LOWER() to each

  FIX: Use a function-based index (PostgreSQL)
  CREATE INDEX idx_email_lower ON users (LOWER(email));

  OR: Store email already lowercase
  INSERT INTO users (email) VALUES (LOWER('Alice@Email.com'));
```

---

<a id="mvcc"></a>
## MVCC — Multi-Version Concurrency Control

### The Problem MVCC Solves

```
Without MVCC (pessimistic locking):

  Transaction 1: UPDATE users SET balance = balance - 100 WHERE id = 1
  → Locks row 1

  Transaction 2: UPDATE users SET balance = balance + 100 WHERE id = 1
  → BLOCKED (waiting for Transaction 1's lock)
  → Transaction 2 waits until Transaction 1 commits

  Problem: Readers also block writers (and vice versa)
  → SELECT * FROM users WHERE id = 1
  → BLOCKED (waiting for Transaction 1's lock)
  → A long write transaction blocks ALL reads

With MVCC (optimistic, multi-version):

  Transaction 1: UPDATE users SET balance = 900 WHERE id = 1
  → Creates a NEW version of row 1 (version 2)
  → Old version (version 1, balance=1000) is kept

  Transaction 2: SELECT * FROM users WHERE id = 1
  → Reads version 1 (balance=1000) — started before Transaction 1
  → NOT BLOCKED. Returns old version.

  Transaction 3: SELECT * FROM users WHERE id = 1
  → Started after Transaction 1 committed
  → Reads version 2 (balance=900)

  RESULT: Readers never block writers. Writers never block readers.
  Each transaction sees a consistent snapshot of data at its start time.
```

### How PostgreSQL MVCC Works

```
PostgreSQL stores multiple row versions (tuples):

  ┌──────────────────────────────────────────────────────┐
  │  HEAP TABLE                                           │
  │                                                       │
  │  Row Header:                                          │
  │  ┌────────────────────────────────────────────────┐  │
  │  │ xmin = transaction that CREATED this tuple      │  │
  │  │ xmax = transaction that DELETED this tuple      │  │
  │  │       (0 = not deleted yet)                     │  │
  │  │ cid  = command ID within transaction            │  │
  │  │ data = actual column values                     │  │
  │  └────────────────────────────────────────────────┘  │
  │                                                       │
  │  Tuple 1: xmin=100, xmax=150, data={name:"Alice v1"} │
  │  Tuple 2: xmin=150, xmax=0,   data={name:"Alice v2"} │
  │  Tuple 3: xmin=120, xmax=0,   data={name:"Bob"}      │
  │                                                       │
  │  Transaction 200 reads:                               │
  │    → Tuple 1: xmin=100 ≤ 200? ✓. xmax=150 ≤ 200? ✓   │
  │      → This tuple was deleted → SKIP                  │
  │    → Tuple 2: xmin=150 ≤ 200? ✓. xmax=0? ✓           │
  │      → This tuple is visible → RETURN                 │
  │    → Tuple 3: xmin=120 ≤ 200? ✓. xmax=0? ✓           │
  │      → Visible → RETURN                               │
  └──────────────────────────────────────────────────────┘

  PROBLEM: Dead tuples accumulate (old versions)
  → Tuple 1 is dead (xmax set), but still takes disk space
  → This is why PostgreSQL needs VACUUM

  VACUUM: Removes dead tuples, reclaims space
  → Vacuum full: rewrites entire table (locks, slow)
  → Autovacuum: Runs automatically in background (non-blocking)
```

### How MySQL InnoDB MVCC Works

```
MySQL uses a different approach: undo logs + read view

  ┌──────────────────────────────────────────────────────┐
  │  InnoDB MVCC                                           │
  │                                                       │
  │  CURRENT DATA (in table):                             │
  │  Row 1: {id:1, balance:900}  ← latest version        │
  │                                                       │
  │  UNDO LOG (rollback segments):                        │
  │  Undo entry: "Row 1 was {id:1, balance:1000}         │
  │               changed by txn 150"                     │
  │  Undo entry: "Row 1 was {id:1, balance:800}          │
  │               changed by txn 120"                     │
  │                                                       │
  │  Transaction 100 reads Row 1:                         │
  │    → Latest version is from txn 150                   │
  │    → But txn 100 started before txn 150               │
  │    → Follow undo log backward:                        │
  │      → Undo txn 150: balance was 1000                 │
  │      → txn 100 < txn 120? → follow further:           │
  │      → Undo txn 120: balance was 800                  │
  │      → Return balance=800                             │
  │                                                       │
  │  Undo logs are purged when no active transaction      │
  │  needs them anymore.                                  │
  └──────────────────────────────────────────────────────┘
```

---

<a id="wal"></a>
## The Write-Ahead Log (WAL)

### Why WAL Exists

```
When you UPDATE a row:

  1. Read the data page from disk into buffer pool (if not cached)
  2. Modify the page in buffer pool
  3. Return "OK" to client

  Problem: The modified page is in RAM (buffer pool).
  If power fails before the page is written to disk → DATA LOSS.

  Solution: Write to WAL BEFORE modifying the page.

  1. Read data page into buffer pool
  2. Write the CHANGE to WAL (append to log file on disk)
     → fsync() — force physical write to disk
  3. Modify the page in buffer pool
  4. Return "OK" to client

  Now if power fails:
  → WAL has the change on disk
  → On restart: replay WAL → reconstruct the change
  → No data loss
```

### WAL Flow

```
  Transaction: UPDATE users SET balance = 900 WHERE id = 1

  ┌──────────┐     ┌──────────┐     ┌──────────┐
  │  Client   │     │  Buffer   │     │  WAL/     │
  │           │     │  Pool     │     │  Redo Log │
  │  UPDATE   │     │  (RAM)    │     │  (Disk)   │
  │     │     │     │           │     │           │
  │     ▼     │     │     ┌─────┘     │           │
  │  ┌─────────────┐│     │     ┌─────┘           │
  │  │ DB Engine    ├─────┼─────┼────►│ Append:    │
  │  │              │     │     │     │ "UPDATE    │
  │  │  1. Write    │─────┼─────┼────►│ users      │
  │  │     to WAL ──────────────────►│ SET        │
  │  │              │     │     │     │ balance=900│
  │  │  2. fsync()  │     │     │     │ WHERE id=1"│
  │  │     (flush   │     │     │     │            │
  │  │      to disk)│     │     │     │ [sync]     │
  │  │              │     │     │     │            │
  │  │  3. Modify   │─────┼─────┘     │            │
  │  │     page in  │     │  Page:    │            │
  │  │     buffer   │     │  id=1     │            │
  │  │     pool     │     │  bal=900  │            │
  │  │              │     │  (dirty)  │            │
  │  │  4. Return   │     │           │            │
  │  │     "OK"     │     │           │            │
  │  └──────────────┘    │           │            │
  │                      │           │            │
  │  Later (background): │           │            │
  │  Checkpoint:         │           │            │
  │  Write dirty pages   │           │            │
  │  from buffer pool    │           │            │
  │  to data files ─────►│──────────►│ Data Files │
  └──────────┘          └──────────┘ └──────────┘
```

### Checkpointing

```
WAL grows forever if we don't clean up:

  Checkpoint: "All data pages modified up to WAL position X
               have been written to data files."

  → Safe to truncate WAL before position X
  → On crash recovery: start from checkpoint, replay WAL from there
  → Reduces recovery time

  PostgreSQL: Checkpoint every checkpoint_timeout (5 min) or
              max_wal_size (1GB)
  MySQL: Checkpoint every innodb_flush_log_at_timeout (1s) —
         very frequent, fast recovery
```

---

<a id="query"></a>
## Query Execution — Parser → Planner → Executor

### The Planner (Most Important Component)

```
SELECT * FROM orders o
JOIN users u ON o.user_id = u.id
WHERE u.country = 'India'
ORDER BY o.created_at DESC
LIMIT 10;

The Planner considers multiple strategies:

  STRATEGY A: Hash Join
    1. Scan users WHERE country='India' → build hash table
    2. Scan orders, probe hash table for matching user_id
    3. Sort results by created_at
    4. Return top 10

  STRATEGY B: Nested Loop + Index
    1. Scan users WHERE country='India' using idx_country
    2. For each user: lookup orders by user_id (index)
    3. Merge results, sort, return top 10

  STRATEGY C: Merge Join (if both tables sorted by user_id)
    1. Scan both tables in user_id order
    2. Merge matching rows
    3. Filter by country, sort by created_at

  Cost estimation for each strategy:
    → Uses table statistics (row count, value distribution, index depth)
    → Estimates disk I/O and CPU for each step
    → Picks the cheapest plan

  PostgreSQL: EXPLAIN ANALYZE shows the plan + actual timing
  MySQL: EXPLAIN shows the plan
```

### EXPLAIN Output

```
PostgreSQL EXPLAIN ANALYZE:

  QUERY PLAN
  ──────────────────────────────────────────────────
  Limit (cost=50.12..50.15 rows=10 width=128)
        (actual time=0.234..0.237 rows=10 loops=1)
    → Sort (cost=50.12..52.50 rows=950 width=128)
           (actual time=0.232..0.234 rows=10 loops=1)
          Sort Key: o.created_at DESC
          Sort Method: top-N heapsort  Memory: 30kB
        → Hash Join (cost=10.50..40.20 rows=950 width=128)
               (actual time=0.120..0.180 rows=950 loops=1)
              Hash Cond: (o.user_id = u.id)
            → Seq Scan on orders o
                   (actual time=0.010..0.050 rows=5000 loops=1)
            → Hash (cost=8.00..8.00 rows=200 width=68)
                   (actual time=0.080..0.080 rows=200 loops=1)
                → Seq Scan on users u
                       Filter: (country = 'India')
                       (actual time=0.005..0.060 rows=200 loops=1)

  Reading this:
    → Scanned users (sequential scan, filtered by country='India')
    → Built hash table
    → Scanned orders, joined via hash
    → Sorted results by created_at
    → Returned top 10
    → Total: 0.237ms
```

---

<a id="mysql-vs-pg"></a>
## MySQL vs PostgreSQL — Real Differences

| Feature | MySQL (InnoDB) | PostgreSQL |
|---------|----------------|------------|
| **Index structure** | Clustered (table = B-tree, ordered by PK) | Heap (unordered) + separate B-tree indexes |
| **Secondary index** | Stores PK → second lookup to get data | Stores CTID → direct pointer to row |
| **UUID primary key** | BAD (random inserts → page splits) | OK (heap doesn't reorder) |
| **Auto-increment PK** | BEST (sequential appends) | Good but less critical |
| **MVCC implementation** | Undo logs (rollback segments) | Multiple tuple versions (xmin/xmax) |
| **Cleanup** | Automatic (purge thread cleans undo log) | VACUUM needed (removes dead tuples) |
| **JSON** | JSON type (basic indexing) | JSONB (binary, indexable, queryable) |
| **Full-text search** | Basic (MATCH AGAINST) | Advanced (tsvector, tsquery, ranking) |
| **Geospatial** | Spatial extensions (limited) | PostGIS (best-in-class) |
| **CTE / Window functions** | Yes (since 8.0) | Yes (since 8.4, more advanced) |
| **Materialized views** | No | Yes |
| **Custom data types** | Limited | Yes (create your own types) |
| **Extensions** | Limited | Rich ecosystem (PostGIS, pgvector, TimescaleDB) |
| **Replication** | Async/Semi-sync (binlog) | Async (logical/physical) |
| **Default isolation** | REPEATABLE READ | READ COMMITTED |

### When to Choose Which

```
Choose MySQL when:
  → High-write OLTP with simple queries
  → Auto-increment primary keys (InnoDB clustered index shines)
  → Team already knows MySQL
  → Need Vitess-style horizontal sharding
  → Read-heavy with many replicas

Choose PostgreSQL when:
  → Complex queries (JSON, GIS, full-text, window functions)
  → Need geospatial (PostGIS)
  → Need AI/ML (pgvector for embeddings)
  → Need materialized views
  → Need custom data types or extensions
  → Analytical queries mixed with OLTP
  → Need time-series (TimescaleDB extension)
```

---

<a id="replication"></a>
## Replication

### MySQL Replication (Binlog-based)

```
  ┌──────────────┐
  │  MASTER       │
  │  (writes)     │
  │               │
  │  Binary Log:  │
  │  [txn1: UPDATE...]
  │  [txn2: INSERT...]
  │  [txn3: DELETE...]
  └───────┬───────┘
          │
          │ Replication thread sends binlog events
          │
  ┌───────▼───────┐  ┌───────────────┐
  │  REPLICA 1    │  │  REPLICA 2    │
  │  (reads)      │  │  (reads)      │
  │               │  │               │
  │  IO Thread:   │  │  IO Thread:   │
  │  Receives     │  │  Receives     │
  │  binlog events│  │  binlog events│
  │               │  │               │
  │  SQL Thread:  │  │  SQL Thread:  │
  │  Replays      │  │  Replays      │
  │  events       │  │  events       │
  └───────────────┘  └───────────────┘

  Replication lag: Time between master commit and replica apply
  → Typically 1-100ms (async)
  → If replica falls behind: stale reads

  Semi-sync replication:
  → Master waits for at least 1 replica to acknowledge
  → Reduces data loss risk (but adds latency)
```

### PostgreSQL Replication

```
  Physical (Streaming) Replication:
  → Stream WAL records from primary to standby
  → Standby replays WAL → exact byte copy of primary
  → Cannot run different schema on standby

  Logical Replication:
  → Decode WAL into logical changes (INSERT/UPDATE/DELETE)
  → Send to subscriber which applies them
  → Can replicate only specific tables
  → Can replicate between different PostgreSQL versions
  → Subscriber is writable (can have its own data too)
```

---

<a id="sharding"></a>
## Sharding at Facebook/Flipkart Scale

### How Facebook Shards MySQL

```
Facebook: 3 billion users on MySQL.

  Cannot fit in one MySQL instance.
  Solution: Shard by user_id.

  ┌──────────────────────────────────────┐
  │            SHARDING LAYER             │
  │                                      │
  │  shard = hash(user_id) % 3000        │
  │                                      │
  │  → user_id 12345 → shard 847         │
  │  → user_id 67890 → shard 2103        │
  └──────┬───────┬───────┬───────────────┘
         │       │       │
    ┌────▼──┐┌───▼──┐┌──▼───┐
    │Shard 0││Shard 1││Shard N│
    │(~1M   ││(~1M   ││(~1M   │
    │ users)││ users)││ users)│
    │MySQL A││MySQL B││MySQL C│
    │Master ││Master ││Master │
    │+Reps  ││+Reps  ││+Reps  │
    └───────┘└───────┘└───────┘

  Each shard:
    → MySQL master + 2-3 replicas
    → Holds ~1 million users
    → User's posts, comments, photos metadata all on same shard

  Cross-shard operations:
    → News Feed: Pre-computed (fanout-on-write) + cached in memcached
    → Search: Separate Elasticsearch index
    → Notifications: Separate system
```

### Cross-Shard Joins — The Hardest Problem

```
  PROBLEM:
    Shard 1: User Alice (id=12345)
    Shard 2: Order #999 (user_id=12345)

    "Get all orders for Alice" requires:
    → Query Shard 1: Get Alice's data
    → Query Shard 2: Get orders for user_id=12345
    → Join in application code

  FACEBOOK'S SOLUTION: Don't join.
    → Denormalize: Store user data redundantly where needed
    → Cache: Pre-compute join results in memcached
    → Async: Use background jobs to sync denormalized data
```

---

<a id="vitess"></a>
## Vitess — How YouTube Scales MySQL

```
Vitess is a database clustering system for MySQL.

  WHAT IT DOES:
    → Makes MySQL horizontally scalable (like a sharding layer)
    → Applications talk to Vitess (not MySQL directly)
    → Vitess routes queries to the right shard

  ┌──────────────────────────────────────────────┐
  │  Application                                  │
  │  (thinks it's talking to one MySQL)           │
  └──────────────┬───────────────────────────────┘
                 │
  ┌──────────────▼───────────────────────────────┐
  │  VTGATE (proxy)                               │
  │                                               │
  │  "SELECT * FROM orders WHERE user_id = 12345" │
  │  → Looks up: user_id=12345 is on shard 3      │
  │  → Routes query to shard 3                    │
  └──────────────┬───────────────────────────────┘
                 │
  ┌──────────────▼───────────────────────────────┐
  │  VTTABLET (sidecar, one per MySQL instance)   │
  │  → Manages the MySQL instance                 │
  │  → Handles schema changes                     │
  │  → Monitoring                                 │
  └──────────────┬───────────────────────────────┘
                 │
  ┌──────────────▼───────────────────────────────┐
  │  MySQL (shard 3)                              │
  └──────────────────────────────────────────────┘

  USED BY: YouTube, Slack, Square, GitHub (formerly)
  → YouTube runs thousands of MySQL shards through Vitess
```

---

<a id="tuning"></a>
## Performance Tuning

### Buffer Pool Sizing (Most Important!)

```
  MySQL: innodb_buffer_pool_size
  PostgreSQL: shared_buffers

  → This is the #1 performance knob.
  → Should be 50-75% of available RAM.

  Why?
    → Every query hits the buffer pool first
    → Bigger buffer pool = more data in RAM = fewer disk reads
    → A 32GB server should have ~20GB buffer pool

  MySQL:
    innodb_buffer_pool_size = 20G

  PostgreSQL:
    shared_buffers = 16GB
    effective_cache_size = 24GB  (hint to planner about OS cache)
```

### Index Strategy

```
RULE 1: Index foreign keys
  → Every FK column should have an index
  → JOINs on unindexed FKs = full table scan per join

RULE 2: Composite indexes for common query patterns
  → Query: WHERE user_id = ? AND status = 'active' ORDER BY created_at
  → Index: (user_id, status, created_at)
  → Leftmost prefix: can serve WHERE user_id=?, WHERE user_id=? AND status=?

RULE 3: Don't over-index
  → Every index slows down writes (index must be updated)
  → 5-10 indexes per table is typical
  → Remove unused indexes (check with monitoring)

RULE 4: EXPLAIN every slow query
  → EXPLAIN ANALYZE SELECT ... → see if index is being used
  → Seq Scan = bad (full table scan)
  → Index Scan = good
```

### Connection Pooling

```
PROBLEM: Each connection uses ~2-10MB of RAM on the server.
  → 1,000 connections = 10GB RAM just for connections
  → PostgreSQL max_connections default = 100 (for good reason)

SOLUTION: PgBouncer (PostgreSQL) / ProxySQL (MySQL)

  ┌──────────┐
  │ 1000 App  │     ┌──────────┐     ┌──────────┐
  │ connectns │────►│PgBouncer │────►│PostgreSQL│
  │           │     │          │     │          │
  │           │     │ Multiplex│     │ 20 conns │
  │           │     │ 1000→20  │     │ (handles │
  │           │     │          │     │  all via │
  │           │     │          │     │  pooling)│
  └──────────┘     └──────────┘     └──────────┘

  PgBouncer holds 1000 client connections but maintains
  only 20 connections to PostgreSQL.
  → Multiplexes queries across the 20 connections.
  → PostgreSQL sees only 20 connections → less RAM.
```

---

<a id="real-apps"></a>
## How Real Companies Use MySQL/PostgreSQL

| Company | DB | How |
|---------|-----|-----|
| **Facebook** | MySQL (sharded) | Thousands of shards, each ~1M users. Custom sharding layer. Memcached for reads. |
| **YouTube** | MySQL + Vitess | Vitess manages thousands of MySQL shards transparently. |
| **Flipkart** | MySQL (sharded) | Orders, products, users. Read replicas during Big Billion Days. |
| **Twitter** | MySQL + Redis | Tweets sharded by user_id. Timelines pre-computed in Redis. |
| **Instagram** | PostgreSQL | User data, media metadata. Uses Redis + Cassandra alongside. |
| **Uber** | PostgreSQL | Trip data, payments (originally; some moved to other DBs). |
| **Spotify** | PostgreSQL | Music catalog, playlists, user data. |
| **Razorpay** | PostgreSQL | Transactions, merchants, settlements. ACID for payments. |
| **Zomato** | PostgreSQL | Restaurants, orders, users. Elasticsearch for search. |
| **Discord** | PostgreSQL | Messages (moved some to ScyllaDB for scale). |

---

<a id="build"></a>
## How YOU Can Build This

### Docker Setup

```bash
# PostgreSQL
docker run --name postgres -e POSTGRES_PASSWORD=secret -p 5432:5432 -d postgres:16

# MySQL
docker run --name mysql -e MYSQL_ROOT_PASSWORD=secret -p 3306:3306 -d mysql:8

# Connect
psql -h localhost -U postgres
mysql -h localhost -u root -p
```

### Schema Design Example

```sql
-- E-commerce schema

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,          -- auto-increment
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE products (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    stock INT DEFAULT 0,
    category VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    total DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INT NOT NULL,
    price DECIMAL(10,2) NOT NULL
);

-- Indexes for common queries
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_price ON products(price);

-- Composite index for user's recent orders
CREATE INDEX idx_orders_user_created
    ON orders(user_id, created_at DESC);
```

### Transaction Example

```sql
-- Place an order (atomic)
BEGIN;

-- Check and lock inventory
SELECT stock FROM products WHERE id = 42 FOR UPDATE;
-- stock = 5 (enough)

-- Deduct inventory
UPDATE products SET stock = stock - 2 WHERE id = 42;

-- Create order
INSERT INTO orders (user_id, total, status)
VALUES (1, 200.00, 'confirmed')
RETURNING id;
-- Returns order_id = 1001

-- Create order items
INSERT INTO order_items (order_id, product_id, quantity, price)
VALUES (1001, 42, 2, 100.00);

COMMIT;  -- All atomic: either all succeed or none

-- If anything fails:
-- ROLLBACK; → everything undone
```

---

## Common Interview Questions

**Q: Explain ACID properties.**

A: Atomicity (all-or-nothing via WAL rollback), Consistency (constraints enforced — FK, CHECK, UNIQUE), Isolation (concurrent transactions don't interfere — via MVCC), Durability (committed data survives crashes — via WAL fsync).

**Q: What is MVCC and why does it matter?**

A: Multi-Version Concurrency Control. Each transaction sees a snapshot of data at its start time. Writers create new versions instead of overwriting. Readers read old versions. This means readers never block writers and writers never block readers — massively improving concurrency compared to pessimistic locking. PostgreSQL implements this with xmin/xmax tuple metadata. MySQL InnoDB uses undo logs.

**Q: What's the difference between a clustered and non-clustered index?**

A: In a clustered index (MySQL InnoDB), the table data IS the index — rows are physically stored in B-tree leaf nodes ordered by the primary key. There's only one clustered index per table. A non-clustered index (PostgreSQL's default) is a separate B-tree that stores pointers to rows in the heap table. PostgreSQL indexes point to rows via CTID. MySQL secondary indexes store the PK, requiring a second lookup.

**Q: How does a B-tree index work?**

A: A B+ tree is a balanced multi-way tree where internal nodes contain only keys (for routing), and leaf nodes contain the actual data or pointers. Leaves are linked in a linked list for efficient range scans. Lookup is O(log N) — for 1 billion rows, about 4 levels. INSERT may cause page splits if a leaf is full. The tree stays balanced — all leaves at the same depth.

**Q: How does sharding work for MySQL?**

A: Split data across multiple MySQL instances by a shard key (usually user_id). Application or middleware (Vitess) determines which shard holds each user's data. Each shard has a master + replicas. Cross-shard queries require application-level joins. Facebook runs thousands of MySQL shards. YouTube uses Vitess to manage MySQL sharding transparently.

**Q: What is the WAL and why is it critical?**

A: The Write-Ahead Log records every change BEFORE it's applied to data pages. On crash recovery, the DB replays the WAL to reconstruct committed changes. This provides Durability (D in ACID). Without WAL, a crash could lose committed transactions that were in the buffer pool but not yet written to data files.

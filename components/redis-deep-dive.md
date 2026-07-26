# Redis — The Complete Deep Dive

> Redis is mentioned **400 times** across this atlas — more than any other technology. This guide explains how it actually works inside, from the single-threaded event loop to the custom data structures to cluster topology.

---

## Table of Contents

1. [What Makes Redis Special](#what-makes-redis-special)
2. [Internal Architecture — The Single-Threaded Event Loop](#event-loop)
3. [Redis Data Structures — How They Work Internally](#data-structures)
4. [Persistence — RDB vs AOF](#persistence)
5. [Replication — Master/Replica](#replication)
6. [Redis Cluster — Sharding & Hash Slots](#cluster)
7. [Sentinel — High Availability](#sentinel)
8. [Pub/Sub](#pubsub)
9. [Redis Streams (Mini-Kafka)](#streams)
10. [Common Patterns & Use Cases](#patterns)
11. [Performance Tuning](#tuning)
12. [Common Pitfalls](#pitfalls)
13. [How Real Companies Use Redis](#real-apps)
14. [How YOU Can Build This](#build)

---

<a id="what-makes-redis-special"></a>
## What Makes Redis Special

### Three Properties That Define Redis

```
1. IN MEMORY — all data lives in RAM, not disk
   → Reads: 0.1-1ms (vs 5-50ms for disk-based DBs)
   → Writes: 0.1-1ms
   → 100,000+ operations per second per instance
   → Trade-off: RAM is 100x more expensive than disk per GB

2. SINGLE-THREADED — one command executes at a time
   → No locks, no race conditions, no context switching
   → Every command is atomic
   → Trade-off: Can't use multiple CPU cores (per instance)

3. DATA STRUCTURES — not just key-value
   → Strings, Hashes, Lists, Sets, Sorted Sets, Streams
   → HyperLogLog, Bitmaps, Geospatial
   → Each has specialized internal encoding for efficiency
   → Operations are built-in (ZADD, HINCRBY, SINTER)
```

### The Speed Comparison

```
Operation                     Latency
──────────────────────────────────────────
Redis GET (in-memory)          0.1ms
PostgreSQL SELECT (cached)     0.5ms
PostgreSQL SELECT (disk)       5-10ms
Cassandra read                 2-10ms
DynamoDB GetItem               5-10ms
S3 GET                         20-50ms

Redis is 50-100x faster than a disk-based database for point lookups.
```

---

<a id="event-loop"></a>
## Internal Architecture — The Single-Threaded Event Loop

### How Redis Processes Commands

```
┌───────────────────────────────────────────────────────┐
│                    REDIS SERVER                        │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │            SINGLE-THREADED EVENT LOOP            │ │
│  │                                                 │ │
│  │  while (true) {                                 │ │
│  │    events = poll_all_sockets()   ← I/O multiplex│ │
│  │    for each event:                             │ │
│  │      parse_command()                           │ │
│  │      execute_command()  ← atomic, no locks     │ │
│  │      write_response()                          │ │
│  │  }                                              │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  Main thread:                                         │
│  ├── Accepts connections                              │
│  ├── Reads commands                                   │
│  ├── Executes commands (sequentially)                 │
│  └── Writes responses                                 │
│                                                       │
│  Background threads (non-blocking):                   │
│  ├── bio_close_file  (close fds from BGSAVE)         │
│  ├── bio_aof_fsync   (fsync AOF file)               │
│  └── bio_lazy_free   (free memory asynchronously)    │
└───────────────────────────────────────────────────────┘
```

### Why Single-Threaded Is Actually Fast

```
MULTI-THREADED DATABASE (e.g., MySQL):
  Thread 1: Lock(table) → Read row → Unlock
  Thread 2: Lock(table) → Write row → Unlock
  Thread 3: Lock(table) → Read row → Unlock

  Overhead per operation:
    - Lock acquisition: ~1μs
    - Context switch: ~5-10μs
    - Cache invalidation: ~5μs
    - Race condition bugs: priceless

SINGLE-THREADED (Redis):
  Command 1: Execute (no lock needed)
  Command 2: Execute (no lock needed)
  Command 3: Execute (no lock needed)

  Overhead per operation:
    - Nothing. Just execute.
    - Zero lock contention
    - Zero context switches
    - Zero race conditions

  Redis processes ~100,000 commands/sec on a single thread.
  The bottleneck is NETWORK I/O, not CPU.

  For multi-core utilization: run multiple Redis instances
  on the same machine (one per core) with Redis Cluster.
```

### The Pipeline — Batching Commands

```
Without pipelining (1 round trip per command):
  Client ──SET key1 val1──► Server  (1ms latency)
  Client ──SET key2 val2──► Server  (1ms latency)
  Client ──SET key3 val3──► Server  (1ms latency)
  Total: 3ms for 3 commands

With pipelining (1 round trip for all):
  Client ──SET key1 / SET key2 / SET key3──► Server
  Client ◄──OK / OK / OK── Server
  Total: 1ms for 3 commands (3x faster)

Pipeline 10,000 commands in one batch:
  Total: ~10ms (vs 10,000ms without pipelining)
  → 1000x faster for bulk operations
```

### The I/O Multiplexing Model

```
Redis uses epoll/kqueue (OS-level I/O multiplexing):

  ┌──────┐
  │Client1│──┐
  │Client2│──┤
  │Client3│──┼──► [epoll] ──► [Single Thread]
  │Client4│──┤      ↑              │
  │Client5│──┘      │              ├── Process Client3's GET
  │                 │              ├── Process Client1's SET
  └── 10,000        │              └── Process Client4's ZADD
      connections    │
                     │
      epoll watches all sockets
      simultaneously (O(1) per check)
      Tells Redis: "Client3 has data ready"
      Redis reads and processes it

  One thread handles 10,000+ connections efficiently
  because it never BLOCKS on any single connection.
```

---

<a id="data-structures"></a>
## Redis Data Structures — How They Work Internally

Redis isn't just a key-value store. Each value can be a rich data structure with its own optimized internal encoding.

### 1. String

```
Command:  SET user:1001 "Alice"
          GET user:1001
          INR page_views          (atomic increment)
          SETEX session:xyz 300 "data"  (with TTL)

Internal Encoding:
  ┌─────────────────────────────────┐
  │  SDS (Simple Dynamic String)    │
  │                                 │
  │  struct sdshdr {               │
  │    int len;      // used bytes  │
  │    int alloc;    // total bytes │
  │    char flags;   // type        │
  │    char buf[];   // actual data │
  │  };                             │
  └─────────────────────────────────┘

  Why SDS instead of C strings?
    - O(1) length check (stored in header)
    - Binary safe (can store \0 in data)
    - Space pre-allocation (reduces realloc)
    - Lazy free space (reuse for next SET)

  Optimization: If value is an integer,
  Redis stores it as a long, not a string.
  INCR/DECR operate on the integer directly.
```

### 2. Hash

```
Command:  HSET user:1001 name "Alice" age 30 email "a@b.com"
          HGET user:1001 name      → "Alice"
          HINCRBY user:1001 age 1  → atomic increment on field

Visual:
  Key: user:1001
  ┌──────────────────────────┐
  │ name  → "Alice"          │
  │ age   → 30               │
  │ email → "a@b.com"        │
  └──────────────────────────┘

Internal Encoding (Redis auto-chooses):
  Small hash (< 512 entries, < 64 byte values):
    → ziplist (compact, contiguous memory block)
    → Space-efficient, cache-friendly

  Large hash:
    → hashtable (separate chaining)
    → O(1) average lookup, more memory overhead
```

### 3. List

```
Command:  LPUSH messages "Hello"     (add to head)
          RPUSH messages "World"     (add to tail)
          LRANGE messages 0 -1       → ["Hello", "World"]
          LPOP messages              → "Hello" (remove from head)
          BLPOP messages 30          → blocking pop (wait 30s)

Visual:
  Key: messages
  ┌──────┐   ┌──────┐   ┌──────┐
  │"Hi"  │◄─►│"Hey" │◄─►│"Yo"  │  (doubly linked list)
  └──────┘   └──────┘   └──────┘
  HEAD                            TAIL

Internal Encoding:
  Small list: ziplist (contiguous)
  Large list: quicklist (linked list of ziplists — best of both)

Use cases:
  - Message queues (LPUSH + BRPOP)
  - Activity feeds (RPUSH new items)
  - Recent items list (LTRIM to keep last N)
```

### 4. Set

```
Command:  SADD tags:user:1001 "tech" "sports" "music"
          SMEMBERS tags:user:1001
          SINTER tags:user:1001 tags:user:1002  (intersection!)

Visual:
  Key: tags:user:1001        Key: tags:user:1002
  ┌──────────────┐           ┌──────────────┐
  │ "tech"       │           │ "tech"       │
  │ "sports"     │           │ "cooking"    │
  │ "music"      │           │ "music"      │
  └──────────────┘           └──────────────┘

  SINTER → {"tech", "music"}  (common tags!)

Internal Encoding:
  Small set (all integers): intset (sorted array of ints)
  Small set (strings):      listpack (compact)
  Large set:                hashtable

Use cases:
  - Tags, categories
  - Unique visitors (SADD user_id)
  - Mutual friends (SINTER)
  - Lottery/random (SRANDMEMBER)
```

### 5. Sorted Set (ZSET) — Redis's Crown Jewel

```
Command:  ZADD leaderboard 100 "Alice" 200 "Bob" 150 "Carol"
          ZREVRANGE leaderboard 0 2   → Top 3 by score
          ZINCRBY leaderboard 50 "Alice"  (increment score)

Visual:
  Key: leaderboard
  ┌───────────────────────────────────┐
  │  Member    │  Score  │  Rank     │
  │────────────┼─────────┼───────────│
  │  "Bob"     │  200    │  1        │
  │  "Carol"   │  150    │  2        │
  │  "Alice"   │  100    │  3        │
  │            │         │ (sorted)  │
  └───────────────────────────────────┘

Internal Encoding:
  Small sorted set: listpack (single contiguous block)
  Large sorted set: skiplist + hash (dual data structure!)

  Why a skiplist (not a balanced tree)?

  ┌─────┐
  │ A:3 │──────────────────────────────────► NULL
  │     │
  │     │─────────►┌─────┐
  │     │           │ B:7 │────────────────► NULL
  │     │           │     │
  │     │           │     │──►┌─────┐
  │     │           │     │    │ C:9 │──► NULL
  └─────┘           └─────┘    └─────┘

  Skip list = multi-level linked list
  → Search: Start at top level, skip large portions
  → O(log N) search (like balanced tree)
  → Simpler to implement than red-black tree
  → Better concurrency characteristics (important for Redis)
  → Range queries are natural (just walk the bottom level)

  Plus a hash table: member → score (for O(1) score lookup)

  ZADD: Update hash table (O(1)) + insert into skiplist (O(log N))
  ZRANGE: Walk skiplist bottom level from start position (O(log N + M))
```

### 6. Bitmaps

```
Command:  SETBIT user:1001:days_active 365 1   (day 365 active)
          GETBIT user:1001:days_active 365     → 1
          BITCOUNT user:1001:days_active       → total days active

Visual: Each bit represents one day
  Bit:   0 1 2 3 4 5 ... 365
  Value: 0 1 0 1 1 0 ... 1

  Storage: 365 bits = 46 bytes (vs 365 booleans = 365 bytes in a DB)
  1 million users × 365 days = 46 MB (fits in one Redis instance!)

Use cases:
  - Daily active user tracking
  - Feature flags per user
  - A/B test assignment
```

### 7. HyperLogLog

```
Command:  PFADD unique_visitors "user1" "user2" "user3"
          PFCOUNT unique_visitors    → ~3 (approximate)

What it does: Counts UNIQUE items with fixed memory.
  → Exact count: Store every item → memory grows linearly
  → HyperLogLog: Approximate count → FIXED 12KB memory

  12KB can count up to 10^9 unique items with ~0.81% error.

Use case: "How many unique users visited today?"
  Exact (SET): Add user_id to set. Memory = N × (user_id size)
  HLL (PFADD): 12KB total. Always. Even for 1 billion users.
```

### 8. Geospatial

```
Command:  GEOADD restaurants 72.8777 19.0760 "Restaurant A"
          GEORADIUS restaurants 72.8 19.0 5 km
          → Returns restaurants within 5km

How it works: Uses sorted sets with geohash encoding.
  → Longitude/Latitude → geohash (a single number)
  → Stored in a sorted set (score = geohash)
  → GEORADIUS = range query on geohash

Use cases:
  - "Find nearby" (Zomato, Swiggy, Uber)
  - Geofencing
  - Distance calculations
```

---

<a id="persistence"></a>
## Persistence — RDB vs AOF

Redis is in-memory, but it CAN persist data to disk for crash recovery.

### RDB (Redis Database) — Point-in-Time Snapshots

```
How RDB works:
  1. Redis forks a child process (identical copy of parent)
  2. Child writes all data to a .rdb file on disk
  3. When done, replaces the old .rdb file

  ┌─────────────┐       fork()        ┌─────────────┐
  │ Main Redis   │ ──────────────────►│ Child Process│
  │ (continues   │                    │ (writes .rdb │
  │  serving)    │                    │  to disk)    │
  └─────────────┘                    └─────────────┘

  Trigger: Every N seconds OR every M changes
    save 900 1     → Save if ≥1 key changed in last 900s
    save 300 10    → Save if ≥10 keys changed in last 300s
    save 60 10000  → Save if ≥10000 keys changed in last 60s

Pros:
  + Compact file (binary, compressed)
  + Fast recovery (load one file)
  + Perfect for backups (copy the .rdb file)
  + Fork is non-blocking (main thread continues serving)

Cons:
  - Data loss between snapshots (up to 15 minutes of writes)
  - Fork can be slow for large datasets (10-20GB → fork takes seconds)
  - Not suitable for data that can't be lost
```

### AOF (Append-Only File) — Every Write Logged

```
How AOF works:
  Every write command is appended to a log file.

  User: SET user:1001 "Alice"
  → Redis executes command
  → Redis appends to AOF file: SET user:1001 "Alice"\n

  User: INCR counter
  → Redis executes command
  → Redis appends: INCR counter\n

  On crash recovery:
  → Redis reads the AOF file
  → Replays every command
  → Data is restored to exact state before crash

AOF fsync policies:
  appendfsync always    → fsync after EVERY write (safest, slowest: ~30% slower)
  appendfsync everysec  → fsync once per second (RECOMMENDED: ≤1s data loss)
  appendfsync no        → Let OS handle it (fastest, risk of data loss)

AOF Rewrite (compaction):
  Over time, AOF file grows unboundedly:
    SET counter 1
    SET counter 2
    SET counter 3
    SET counter 4
    ...
    SET counter 100   ← Current value

  100 commands, but only "counter=100" matters.

  AOF Rewrite: Fork child → output current state as minimal commands
    SET counter 100   ← Just one command!

  Result: Compact AOF file with same data.
```

### Which to Choose?

```
┌────────────┬───────────────────┬───────────────────────┐
│            │ RDB               │ AOF                   │
├────────────┼───────────────────┼───────────────────────┤
│ Data loss  │ Up to 15 min      │ Up to 1 sec (everysec)│
│ File size  │ Small (binary)    │ Larger (text commands)│
│ Recovery   │ Fast              │ Slower (replay)       │
│ CPU cost   │ Low (fork)        │ Higher (append every) │
│ Use case   │ Cache, backups    │ Session data, critical│
└────────────┴───────────────────┴───────────────────────┘

BEST PRACTICE: Use both!
  RDB for periodic snapshots (backup safety net)
  AOF for durability (minimal data loss)
  Redis can run both simultaneously.
```

---

<a id="replication"></a>
## Replication — Master/Replica

```
┌──────────────┐                    ┌──────────────┐
│  MASTER      │   async replicate  │  REPLICA 1   │
│  (read+write)│──────────────────►│  (read only)  │
└──────┬───────┘                    └──────────────┘
       │
       │  async replicate
       ▼
┌──────────────┐
│  REPLICA 2   │
│  (read only)  │
└──────────────┘

How it works:

1. REPLICA sends PSYNC to MASTER
2. MASTER starts background save (BGSAVE)
3. MASTER sends .rdb file to REPLICA
4. REPLICA loads .rdb (now has snapshot)
5. MASTER sends all new commands since BGSAVE started
6. REPLICA applies them (stays in sync)

Ongoing:
  MASTER ──streams all write commands──► REPLICA (in real-time)

Read scaling:
  App writes → MASTER
  App reads  → any REPLICA (distribute read load)

  1 master + 5 replicas = 5x read capacity
```

### Asynchronous Replication (Trade-off)

```
Replication is ASYNC:
  Master writes data → responds to client immediately
  → Replication to replicas happens in background

  Risk: If master crashes before replicas catch up:
  → Last few writes lost

  Why async?
  → Sync replication would mean: master waits for ALL replicas
  → If any replica is slow → master is slow
  → If any replica is down → master blocks
  → Async = master is always fast
```

---

<a id="cluster"></a>
## Redis Cluster — Sharding & Hash Slots

Single Redis maxes out at one CPU core. To use multiple cores/servers, use **Redis Cluster**.

### How Redis Cluster Shards Data

```
Redis Cluster uses HASH SLOTS:

  Total slots: 16,384 (0 to 16,383)

  Each key is assigned to a slot:
    slot = CRC16(key) % 16384

  Each cluster node owns a range of slots:

  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Node A   │  │ Node B   │  │ Node C   │
  │ Slots:   │  │ Slots:   │  │ Slots:   │
  │ 0-5460   │  │ 5461-10922│ │ 10923-16383│
  │ (5461    │  │ (5462    │  │ (5461    │
  │  slots)  │  │  slots)  │  │  slots)  │
  └──────────┘  └──────────┘  └──────────┘

  Key "user:1001" → CRC16 → slot 4813 → Node A
  Key "order:55"  → CRC16 → slot 8210 → Node B
  Key "cart:77"   → CRC16 → slot 12000 → Node C
```

### Redis Cluster Topology

```
                    ┌──────────┐
                    │  Node A   │
                    │  Master   │
                    │  Slots:   │
                    │  0-5460   │
                    └──┬───┬───┘
                       │   │
              ┌────────┘   └────────┐
              │                     │
  ┌───────────▼─┐              ┌───▼──────────┐
  │ Node A'      │              │ Node B'       │
  │ (Replica of  │              │ (Replica of   │
  │  Node B)     │              │  Node C)      │
  └──────────────┘              └───────────────┘

  Full cluster (6 nodes, 3 masters + 3 replicas):

  Master A (slots 0-5460)    ──replicated by──► Replica A'
  Master B (slots 5461-10922) ──replicated by──► Replica B'
  Master C (slots 10923-16383)──replicated by──► Replica C'

  If Master B dies → Replica B' is promoted to Master
  → Cluster continues operating
```

### MOVED Redirection

```
Client sends GET user:1001 to Node C:
  Node C calculates: slot 4813 → "That's Node A's slot"
  Node C responds: -MOVED 4813 10.0.0.1:6379

Client redirects to Node A:
  GET user:1001 → Node A → "Alice"

  Smart clients cache the slot map.
  After the first MOVED, they know where each slot lives.
  Future requests go directly to the right node.
```

### Hash Tags — Keeping Related Keys Together

```
Problem: You want to do MGET user:1001:name user:1001:email
  → These might be on different nodes!
  → Redis Cluster doesn't allow multi-key operations across slots.

Solution: Hash Tags

  Key: {user:1001}:name  → slot = CRC16("user:1001") % 16384
  Key: {user:1001}:email → same slot! (only {user:1001} is hashed)

  Now both keys are on the same node.
  MGET {user:1001}:name {user:1001}:email works!
```

---

<a id="sentinel"></a>
## Sentinel — High Availability

Redis Sentinel monitors a standalone Redis master/replica setup and provides automatic failover.

```
                    ┌─────────────────┐
                    │  Sentinel 1     │
                    │  Sentinel 2     │  (3 or 5 sentinels,
                    │  Sentinel 3     │   on separate machines)
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Master Redis    │
                    │  (monitored)     │
                    └────┬───────┬────┘
                         │       │
                ┌────────▼┐    ┌─▼──────────┐
                │ Replica 1│   │ Replica 2   │
                └──────────┘   └────────────┘

Sentinel's jobs:
  1. MONITORING: Continuously ping master and replicas
  2. NOTIFICATION: Alert admins on events
  3. AUTOMATIC FAILOVER: If master dies → promote a replica
  4. CONFIGURATION PROVIDER: Tell clients the new master address

Failover process:
  1. Sentinel detects master is down (missed 3 heartbeats)
  2. Sentinels vote (quorum needed: e.g., 2 of 3)
  3. They agree: "Master is objectively down" (ODOWN)
  4. Sentinel leader selected
  5. Leader promotes most up-to-date replica to master
  6. Other replicas reconfigured to follow new master
  7. Clients notified of new master address
  8. Old master (when it recovers) becomes a replica
```

---

<a id="pubsub"></a>
## Pub/Sub

```
Redis Pub/Sub: Real-time message broadcasting

  Publisher                Redis                  Subscribers
     │                       │                        │
     │──PUBLISH news "Hi"──►│──push to all subs────►│ Sub A
     │                       │──────────────────────►│ Sub B
     │                       │──────────────────────►│ Sub C

Pattern Subscription:
  PSUBSCRIBE news:*       → matches "news:sports", "news:tech"
  PSUBSCRIBE user:*:msg   → matches "user:1001:msg"

Key limitation:
  → Messages are NOT persisted
  → If a subscriber is disconnected, it misses messages
  → If no subscribers, message is dropped

This is why Redis Streams exist (for persistence).
```

---

<a id="streams"></a>
## Redis Streams (Mini-Kafka)

Redis Streams combine Pub/Sub with persistence. Like a mini-Kafka.

```
Commands:
  XADD events * type login user_id 1001     → Add event with auto-ID
  XADD events * type purchase user_id 1001   → Add another
  XRANGE events - +                          → Read all events
  XREAD events COUNT 10 BLOCK 5000           → Read 10, wait up to 5s

Stream contents:
  ┌─────────────────────────────────────────┐
  │  ID          │ Fields                    │
  │──────────────┼───────────────────────────│
  │ 1690000000-0 │ type=login, user_id=1001  │
  │ 1690000001-0 │ type=purchase, user_id=1001│
  │ 1690000002-0 │ type=logout, user_id=1001  │
  └─────────────────────────────────────────┘

  ID format: <timestamp>-<sequence>
  → Unique, monotonically increasing (like Kafka offsets)

Consumer Groups (like Kafka):
  XGROUP CREATE events analytics $
  XREADGROUP GROUP analytics worker-1 COUNT 10 STREAMS events >

  → Multiple workers in a group divide events (like Kafka)
  → Each event delivered to exactly one worker in the group
  → Pending entries list (PEL) tracks unacked events
  → XACK events <id> to acknowledge processing

Redis Streams vs Kafka:
  Redis Streams: Simpler, in-process, microsecond latency, <100K events/sec
  Kafka:         Complex, separate cluster, millisecond latency, millions/sec
```

---

<a id="patterns"></a>
## Common Patterns & Use Cases

### Pattern 1: Cache-Aside (Most Common)

```python
async def get_user(user_id):
    # 1. Try cache
    cached = redis.get(f"user:{user_id}")
    if cached:
        return json.loads(cached)

    # 2. Cache miss → query database
    user = await db.query("SELECT * FROM users WHERE id = ?", user_id)

    # 3. Store in cache with TTL
    redis.setex(f"user:{user_id}", 300, json.dumps(user))  # 5 min TTL

    return user

async def update_user(user_id, data):
    # 1. Update database
    await db.update("users", user_id, data)
    # 2. Delete cache (next read will re-populate)
    redis.delete(f"user:{user_id}")
```

### Pattern 2: Distributed Lock

```
Acquiring a lock:
  SET lock:resource_1 "owner_id" NX EX 10

  NX = Only set if key doesn't exist (if NOT exists)
  EX 10 = Expire after 10 seconds (auto-release if holder crashes)

  If returns "OK" → lock acquired
  If returns nil → someone else holds the lock

Releasing a lock (safely, with Lua script):
  -- Only delete if we're the owner
  if redis.call("GET", KEYS[1]) == ARGV[1] then
      return redis.call("DEL", KEYS[1])
  else
      return 0
  end

  Why Lua? Because GET + DEL must be atomic.
  If they're separate commands, another client could grab the lock
  between our GET and DEL.
```

### Pattern 3: Rate Limiting (Fixed Window)

```python
def is_rate_limited(user_id, max_requests=100, window_seconds=60):
    key = f"rate:{user_id}:{int(time.time() / window_seconds)}"
    current = redis.incr(key)
    if current == 1:
        redis.expire(key, window_seconds)
    return current > max_requests

# User can make max 100 requests per 60-second window
if is_rate_limited(user_id):
    return "429 Too Many Requests"
```

### Pattern 4: Rate Limiting (Sliding Window via Sorted Set)

```python
def sliding_window_rate_limit(user_id, max_requests=100, window=60):
    key = f"rate:sliding:{user_id}"
    now = time.time()
    cutoff = now - window

    # Remove requests older than the window
    redis.zremrangebyscore(key, 0, cutoff)

    # Count requests in current window
    count = redis.zcard(key)

    if count >= max_requests:
        return False  # Rate limited

    # Add current request
    redis.zadd(key, {str(uuid.uuid4()): now})
    redis.expire(key, window)
    return True
```

### Pattern 5: Leaderboard

```
ZADD game_scores 9500 "player_1" 8700 "player_2" 9200 "player_3"

# Top 10 players:
ZREVRANGE game_scores 0 9 WITHSCORES
→ [("player_1", 9500), ("player_3", 9200), ("player_2", 8700)]

# A player's rank:
ZREVRANK game_scores "player_1"  → 0 (rank 1)

# Update score:
ZINCRBY game_scores 500 "player_2"  → 9200 (new score)
```

### Pattern 6: Session Store

```
# Store session with 30-minute expiry
HSET session:abc123 user_id 1001 ip "1.2.3.4" device "iPhone"
EXPIRE session:abc123 1800  # 30 minutes

# Check session (and extend)
TTL session:abc123  → 1500 (15 min remaining)
EXPIRE session:abc123 1800  # Extend by 30 min (sliding session)

# Delete session (logout)
DEL session:abc123
```

---

<a id="tuning"></a>
## Performance Tuning

### Memory Optimization

```
1. Use appropriate encodings:
   - Hashes < 512 fields → ziplist (10x less memory)
   - Sets of integers → intset (5x less memory)
   - Small sorted sets → listpack

   Tune thresholds:
   hash-max-ziplist-entries 512
   hash-max-ziplist-value 64
   zset-max-ziplist-entries 128

2. Use short keys:
   "u:1001" instead of "user:1001:detailed:profile"
   → Saves bytes per key × millions of keys = GBs

3. Use appropriate data types:
   BAD:  SET user:1001:field1 "val1" (5 separate keys)
   GOOD: HSET user:1001 field1 "val1" (1 key with 5 fields)

4. Enable active memory management:
   maxmemory 8gb
   maxmemory-policy allkeys-lru  → evict least recently used
   # Options: noeviction, allkeys-lru, volatile-lru, allkeys-lfu, allkeys-random
```

### Pipelining and MGET/MSET

```
BAD (100 round trips):
  for key in keys:
      val = redis.get(key)

GOOD (1 round trip):
  values = redis.mget(keys)  # Multi-GET

ALSO GOOD (pipeline):
  pipe = redis.pipeline()
  for key in keys:
      pipe.get(key)
  values = pipe.execute()
```

### When to Use Pipeline vs MGET/MSET

```
MGET/MSET:  Same command, multiple keys. Atomic.
            All keys must be on same slot (cluster limitation).

Pipeline:   Multiple commands batched. Non-atomic.
            Each command can be different type.
            Works across cluster slots.
```

---

<a id="pitfalls"></a>
## Common Pitfalls

### Pitfall 1: KEYS * in Production

```
KEYS *  → Returns ALL keys matching pattern.

Problem:
  1 million keys → Redis blocks for 1-2 seconds scanning all keys
  → ALL other clients blocked during the scan
  → Production frozen

Fix: Use SCAN (iterative, non-blocking)
  SCAN 0 MATCH user:* COUNT 100
  → Returns a batch of 100 keys + cursor for next batch
  → Non-blocking, safe for production
```

### Pitfall 2: Big Keys

```
A single key holds a 1GB list:
  LPUSH huge_list "item"  (called 10 million times)

Problems:
  - DEL huge_list → blocks Redis for several seconds
  - Network transfer of 1GB → timeout
  - Memory fragmentation
  - Migration to another node fails

Fix:
  - Split into smaller keys: huge_list:1, huge_list:2, ...
  - Use UNLINK instead of DEL (async deletion)
  - Monitor key sizes: redis-cli --bigkeys
```

### Pitfall 3: Cache Stampede (Thundering Herd)

```
Popular key expires → 1000 requests arrive simultaneously
→ All 1000 see cache MISS
→ All 1000 query the database
→ Database overwhelmed

Fix: Probabilistic Early Expiration (XFetch algorithm)
  → Randomly expire key slightly before actual TTL
  → One request rebuilds cache, others serve stale data
  → Prevents stampede

Fix: Mutex/Lock
  → First MISS acquires lock, queries DB, populates cache
  → Other requests wait for lock release, then read cache
```

---

<a id="real-apps"></a>
## How Real Companies Use Redis

| Company | Redis Usage | Scale |
|---------|------------|-------|
| **Twitter** | Timeline cache (pre-computed feeds in Redis lists) | Billions of keys |
| **Instagram** | Feed, notifications, story pre-computation | 100+ Redis servers |
| **Tinder** | Sorted sets for "people near you" (geo + scoring) | Millions of ZADDs |
| **Stack Overflow** | Cache for questions, answers, user profiles | ~99% cache hit rate |
| **GitHub** | Pub/Sub for real-time notifications | Thousands of channels |
| **Zomato** | Real-time order tracking, restaurant availability | ~100K concurrent sessions |
| **PhonePe** | Rate limiting, idempotency keys, UPI request cache | 10K+ TPS |
| **Razorpay** | Distributed locks, payment state machine | 5K TPS |
| **Slack** | Real-time typing indicators, presence | 30M+ connections |
| **Discord** | Sorted sets for message ordering, Redis Cluster | 150M users |

---

<a id="build"></a>
## How YOU Can Build This

### Level 1: Single Redis Instance

```bash
# Install and run
docker run --name redis -p 6379:6379 -d redis:7

# Connect
redis-cli -h localhost -p 6379

# Basic operations
SET mykey "Hello"
GET mykey
HSET user:1 name "Alice" age 30
HGETALL user:1
ZADD leaderboard 100 "Alice" 200 "Bob"
ZREVRANGE leaderboard 0 -1 WITHSCORES
```

### Level 2: Redis with Persistence + Cache Pattern (Python)

```python
import redis
import json

r = redis.Redis(host='localhost', port=6379, decode_responses=True)

class Cache:
    def __init__(self, redis_client, ttl=300):
        self.r = redis_client
        self.ttl = ttl

    def get_or_set(self, key, fetch_func):
        """Cache-aside pattern: try cache, fall back to function."""
        cached = self.r.get(key)
        if cached:
            return json.loads(cached)

        value = fetch_func()  # Query database
        self.r.setex(key, self.ttl, json.dumps(value))
        return value

    def invalidate(self, key):
        """Delete cache entry when data changes."""
        self.r.delete(key)

# Usage
cache = Cache(r, ttl=300)

user = cache.get_or_set(
    "user:1001",
    lambda: db.query("SELECT * FROM users WHERE id = 1001")
)
```

### Level 3: Redis Cluster (Docker Compose)

```yaml
version: '3'
services:
  redis-node-1:
    image: redis:7
    command: redis-server --port 7000 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports: ["7000:7000"]
  redis-node-2:
    image: redis:7
    command: redis-server --port 7001 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports: ["7001:7001"]
  redis-node-3:
    image: redis:7
    command: redis-server --port 7002 --cluster-enabled yes --cluster-config-file nodes.conf --cluster-node-timeout 5000 --appendonly yes
    ports: ["7002:7002"]

# After starting:
# redis-cli --cluster create localhost:7000 localhost:7001 localhost:7002 --cluster-replicas 0
```

---

## Common Interview Questions

**Q: Why is Redis single-threaded and still fast?**

A: Redis uses a single-threaded event loop with I/O multiplexing (epoll). This avoids lock contention, context switching, and race conditions — the overhead that makes multi-threaded databases slower per operation. Since data is in RAM, the bottleneck is network I/O, not CPU. Redis processes ~100K commands/sec on one thread. For multi-core utilization, run multiple Redis instances per machine with Redis Cluster (one instance per core).

**Q: What happens when Redis runs out of memory?**

A: Depends on `maxmemory-policy`:
- `noeviction` (default): Returns errors on writes. Read-only still works.
- `allkeys-lru`: Evicts the least recently used key (best for cache).
- `allkeys-lfu`: Evicts least frequently used (good for stable popular content).
- `volatile-lru`: Only evicts keys with TTL set.
- `volatile-ttl`: Evicts the key with the nearest expiration.
For a pure cache: `allkeys-lru`. For mixed data+cache: `volatile-lru`.

**Q: Explain Redis persistence options.**

A: Two mechanisms:
1. **RDB**: Point-in-time snapshots via fork. Compact binary file. Can lose up to 15 minutes of data between snapshots. Good for backups.
2. **AOF**: Appends every write command to a log file. `appendfsync everysec` loses at most 1 second of data. AOF Rewrite compacts the file periodically.
Best practice: Use both. RDB for backups, AOF for durability.

**Q: How does Redis Cluster handle a node failure?**

A: Each master has a replica. The cluster uses a gossip protocol for nodes to detect each other's status. When the majority of nodes agree a master is down, its replica is promoted to master. The new master takes over the failed master's hash slots. If both a master and its replica are down, the slots are unavailable (cluster is partially down). The cluster self-heals when nodes come back.

**Q: What's the difference between Redis Pub/Sub and Redis Streams?**

A: Pub/Sub is fire-and-forget. Messages are not persisted — if a subscriber is offline, it misses messages. Multiple subscribers each get a copy. Streams are persistent. Messages have IDs and can be re-read. Consumer groups allow load-balanced consumption (like Kafka). Streams also support acknowledgment and pending entries lists. Use Pub/Sub for real-time notifications; use Streams for durable event logs.

**Q: How do you implement a distributed lock in Redis?**

A: `SET lock_key unique_value NX EX 10`. The NX ensures the key doesn't already exist (only one client can acquire). The EX 10 auto-expires the lock (prevents deadlocks if the holder crashes). To release: use a Lua script that checks the value matches (to prevent releasing someone else's lock) and then deletes. For higher reliability, use Redlock (acquire locks on multiple independent Redis instances).

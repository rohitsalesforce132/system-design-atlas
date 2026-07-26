# Caching

## What It Is (Analogy First)

Imagine you're writing a research paper. Every time you need a fact, you walk to the library (takes 20 minutes). After doing this a few times, you start writing key facts on sticky notes next to your desk (takes 2 seconds).

- **Library** = Database (slow, permanent storage)
- **Sticky notes** = Cache (fast, temporary storage)

A cache stores frequently accessed data in a fast layer so you don't have to hit the slow database every time.

```
User request ──► [CACHE] ──► HIT? ──► Return immediately (1ms)
                    │
                    └─MISS──► [DATABASE] ──► Return + store in cache (50ms)
```

## Why Caching Is the #1 Scaling Technique

| Operation | Latency | Compared to cache |
|-----------|---------|-------------------|
| Read from Redis cache | **0.1 - 1 ms** | Baseline |
| Read from in-memory cache | **0.01 ms** | 10x faster |
| Read from SSD (database) | **0.1 - 1 ms (seek) + processing** | 10-100x slower |
| Read from HDD | **5-10 ms** | 500-1000x slower |
| Network call to another service | **1-50 ms** | 10-500x slower |

A cache hit is **10-100x faster** than a database read. If 80% of your traffic hits the cache, your database load drops by 80%.

## The 80/20 Rule (Pareto Principle)

In most apps, **20% of data gets 80% of traffic**:
- Twitter: 20% of users generate 80% of tweets.
- YouTube: 20% of videos get 80% of views.
- Amazon: 20% of products get 80% of sales.

This means caching just a fraction of data gives massive speedup.

## Types of Caching (By Location)

### 1. Client-Side Cache (Browser)
```
Browser ──request──► Server
Browser ◄─response── Server (with Cache-Control: max-age=3600)

Next 1 hour:
Browser checks local cache → HIT → no request sent at all
```

**Headers that control this:**
- `Cache-Control: max-age=3600` (cache for 1 hour)
- `Cache-Control: no-cache` (always validate with server)
- `ETag: "abc123"` (server gives a version hash; browser sends `If-None-Match` → server says 304 Not Modified if unchanged)

### 2. CDN Cache (Edge)
```
User in Mumbai ──► Mumbai CDN Edge ──► HIT? Return (5ms)
                                        │
                                        └─MISS─► Origin Server (Virginia) (100ms)
```

CDN caches static content at edge locations worldwide. Users get content from a server physically near them.

### 3. Application-Level Cache (Redis/Memcached)
```
App Server ──► Redis (key: "user:1234") ──► HIT? Return (0.5ms)
                                                    │
                                                    └─MISS─► PostgreSQL (50ms)
```

This is where the biggest gains are. Popular tools:
- **Redis:** In-memory key-value store. Most popular. Supports data structures (lists, sets, sorted sets, hashes).
- **Memcached:** Simpler, multi-threaded, great for pure key-value.
- **Hazelcast / Apache Ignite:** Distributed in-memory grids.

### 4. Database Cache
Most databases have internal caches:
- PostgreSQL: `shared_buffers` (caches recently accessed pages in RAM)
- MySQL: `InnoDB buffer pool`
- Cassandra: Row cache, key cache

## Caching Patterns (How to Read/Write)

### Pattern 1: Cache-Aside (Lazy Loading)
**Most common.** Application manages the cache.

```
READ:
1. App checks Redis for key "user:1234"
2. If HIT → return cached value
3. If MISS → query database → store result in Redis (with TTL) → return

WRITE:
1. Update database
2. Delete cache entry (next read will re-populate it)
```

**Pros:** Simple, only caches what's actually requested.
**Cons:** Cache miss is slow (two reads: cache + DB). Stale data possible between DB update and cache delete.

### Pattern 2: Write-Through
**App writes to cache and DB simultaneously.**
```
WRITE:
1. Write to cache
2. Write to database
3. Return success

READ:
1. Read from cache (almost always a hit)
```

**Pros:** Cache is never stale. No miss penalty.
**Cons:** Write latency higher (two writes). Wastes space on rarely-read data.

### Pattern 3: Write-Behind (Write-Back)
**App writes to cache first, then asynchronously writes to DB.**
```
WRITE:
1. Write to cache → return success immediately
2. Background job writes to DB later (batch writes for efficiency)
```

**Pros:** Extremely fast writes. Good for write-heavy workloads (likes, view counts).
**Cons:** Data loss risk if cache crashes before DB write. Complex to implement.

## Cache Eviction Policies

Cache has limited memory. When full, what do you remove?

| Policy | How it works | Best for |
|--------|-------------|----------|
| **LRU (Least Recently Used)** | Remove item not accessed for longest time | General purpose, most common |
| **LFU (Least Frequently Used)** | Remove item accessed least often | Stable, popular content |
| **FIFO (First In First Out)** | Remove oldest item regardless of access | Simple use cases |
| **TTL (Time to Live)** | Item auto-expires after set time | Session data, time-sensitive data |
| **Adaptive Replacement** | Balances freq + recency | Complex workloads |

## Common Caching Problems

### Problem 1: Cache Penetration (Hitting non-existent keys)
```
Attacker requests user_id = -1 (doesn't exist)
→ Cache MISS → hits database → database returns NULL → nothing cached
→ Next request for -1 → cache MISS → hits database again
→ Thousands of these → database overloaded
```

**Fix:** Cache NULL results with short TTL (e.g., `cache["user:-1"] = NULL, TTL=60s`)

### Problem 2: Cache Avalanche
```
All cache entries expire at same time → thousands of requests hit DB simultaneously
```

**Fix:** Add random jitter to TTLs (ecache["user:1"].TTL = 3600 + random(0, 300))

### Problem 3: Cache Stampede (Thundering Herd)
```
A popular item expires → 1000 requests arrive → all see MISS → all query DB → 1000 DB queries for same data
```

**Fix:** Use mutex/lock. First request locks, queries DB, populates cache. Others wait.

### Problem 4: Stale Data
```
Database updated → cache still has old data → user sees stale content
```

**Fix:**
1. Delete cache on DB write (cache-aside).
2. Use write-through if data must be fresh.
3. Accept eventual consistency for some data (e.g., view counts don't need to be exact).

## Multi-Layer Caching at Scale

This is how Netflix/YouTube actually cache:

```
User
  │
  ├─► Browser Cache (static assets: images, CSS, JS — TTL: 1 year)
  │
  ├─► ISP Cache (ISP caches popular content)
  │
  ├─► CDN Edge Node (Mumbai POP — video segments, images — TTL: hours)
  │
  ├─► Origin Shield (single cache layer before origin servers — reduces origin load)
  │
  ├─► Redis Cluster (API responses, user data — TTL: minutes)
  │
  └─► Database (source of truth)
```

## Real-World Examples

| Company | Cache Usage |
|---------|------------|
| **Facebook** | Built **Tao** — a graph cache that caches social relationships. Uses Memcached (thousands of instances). |
| **Twitter** | Timeline generation cached in Redis. Tweets cached individually + timelines pre-computed. |
| **Netflix** | EVCache (distributed cache built on Memcached). Caches user profiles, recommendations, metadata. |
| **Instagram** | Redis for feed, notifications, story pre-computation. |
| **Amazon** | Multi-layered cache for product pages. Cache hit rate target: 99%+ for product lookups. |
| **WhatsApp** | Offline message storage in SQLite on device. Server-side message queues in Erlang. |

## How YOU Can Build This

### Level 1: In-Process Cache (Node.js/Python)
```javascript
// Simple LRU cache in your app process
const cache = new Map();
const TTL = 300; // 5 minutes

async function getUser(id) {
    const key = `user:${id}`;
    const cached = cache.get(key);
    if (cached && Date.now() - cached.time < TTL * 1000) {
        return cached.value; // CACHE HIT
    }
    const user = await db.query('SELECT * FROM users WHERE id = ?', [id]);
    cache.set(key, { value: user, time: Date.now() });
    return user;
}
```

### Level 2: Redis (Single Instance)
```python
import redis
import json

r = redis.Redis(host='localhost', port=6379)

async def get_user(user_id):
    # Try cache first
    cached = r.get(f"user:{user_id}")
    if cached:
        return json.loads(cached)

    # Cache miss → query database
    user = await db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)
    r.setex(f"user:{user_id}", 300, json.dumps(user))  # TTL: 5 min
    return user
```

### Level 3: Redis Cluster (Multi-Node)
```
Redis Cluster with consistent hashing:
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Redis A    │  │  Redis B    │  │  Redis C    │
│ keys 0-33%  │  │ keys 34-66% │  │ keys 67-100%│
└─────────────┘  └─────────────┘  └─────────────┘
     │                  │                │
  Replica A'        Replica B'       Replica C'
```

## Common Interview Questions

**Q: When should you NOT cache?**
A: Don't cache when:
1. Data changes very frequently (caching wastes effort).
2. Data is rarely read (cache is never hit).
3. Data must always be 100% accurate (e.g., bank balances — use write-through or skip cache).
4. Dataset is tiny and fits in DB memory anyway.

**Q: What's the ideal cache hit rate?**
A: Target **90%+** for read-heavy apps. Below 40% means caching is wasting resources.

**Q: Redis vs Memcached — how to choose?**
A:
- **Redis:** Data structures (lists, sorted sets), persistence, replication, pub/sub. Choose for most cases.
- **Memcached:** Simpler, multi-threaded, pure key-value. Choose for extreme simplicity + multi-core utilization.

**Q: How do you keep cache consistent with DB?**
A: Three approaches:
1. **Cache-aside with TTL:** Update DB, delete cache. Accept slight staleness (seconds). Most common.
2. **Write-through:** Always update cache + DB together. Never stale, but slower writes.
3. **Event-driven invalidation:** Database change data capture (CDC) → message → invalidate cache. Complex but powerful.

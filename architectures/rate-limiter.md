# Architecture: Distributed Rate Limiter

> How to design a distributed rate limiting service that handles 100,000+ requests/sec, supports multiple algorithms (token bucket, sliding window, fixed window), and coordinates across multiple server instances.

---

## Table of Contents

1. [Problem Statement & Requirements](#requirements)
2. [Capacity Estimation](#capacity)
3. [High-Level Architecture](#architecture)
4. [Component Selection](#components)
5. [Algorithms Deep Dive](#algorithms)
6. [API Design](#api)
7. [Request Flow](#flow)
8. [Scaling Strategy](#scaling)
9. [Failure Modes & Mitigation](#failures)
10. [Trade-off Analysis](#tradeoffs)

---

<a id="requirements"></a>
## 1. Problem Statement & Requirements

### Functional Requirements
```
- Limit API requests per user/IP/API-key
- Support multiple algorithms: token bucket, sliding window, fixed window
- Configurable limits per endpoint (e.g., /search: 10/sec, /checkout: 2/sec)
- Burst handling (allow short bursts above average rate)
- Multi-tenant (each tenant has their own limits)
- Return remaining quota and reset time in headers
- Dashboard for usage analytics
```

### Non-Functional Requirements
```
- Latency: < 1ms added per request (rate check must be fast)
- Throughput: 100,000+ rate checks/sec
- Accuracy: Strict enforcement (no request should exceed limit)
- Availability: 99.99% (if rate limiter is down, all APIs are down)
- Consistency: Distributed — same limit enforced across all API instances
```

---

<a id="capacity"></a>
## 2. Capacity Estimation

```
ASSUMPTIONS:
  - 100,000 API requests/sec across the platform
  - Each request needs a rate check → 100,000 rate checks/sec
  - 1 million unique users/IPs to track
  - Average 5 rules per user (different limits per endpoint)

STORAGE (Redis):
  - Per-user per-rule counter: ~50 bytes in Redis
  - 1M users × 5 rules × 50 bytes = 250 MB (trivial for Redis)

  Token bucket per user:
    key: "ratelimit:user:12345:search"
    value: { tokens: 8, last_refill: timestamp }
    → ~100 bytes per bucket

  1M users × 5 rules = 5M keys × 100 bytes = 500 MB

THROUGHPUT:
  - 100,000 INCR/GET operations/sec on Redis
  - Single Redis instance handles ~100K ops/sec
  - Use Redis Cluster for redundancy and more headroom

NETWORK:
  - Each rate check: ~200 bytes request + 200 bytes response = 400 bytes
  - 100K/sec × 400 bytes = 40 MB/sec
```

---

<a id="architecture"></a>
## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    RATE LIMITER SYSTEM                            │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │ API Server│  │ API Server│  │ API Server│  (N instances)     │
│  │ Instance 1│  │ Instance 2│  │ Instance 3│                     │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘                      │
│        │             │             │                              │
│        ▼             ▼             ▼                              │
│  ┌──────────────────────────────────────────┐                   │
│  │       RATE LIMIT MIDDLEWARE              │                   │
│  │       (runs in-process, as a library)     │                  │
│  │                                           │                  │
│  │  Before each API request:                 │                  │
│  │    1. Check rate limit for this user      │                  │
│  │    2. If allowed → proceed to API         │                  │
│  │    3. If denied → return 429              │                  │
│  └──────────────────┬───────────────────────┘                   │
│                     │                                           │
│         ┌───────────┴───────────┐                               │
│         ▼                       ▼                               │
│  ┌──────────────┐       ┌──────────────┐                       │
│  │  LOCAL CACHE  │       │  REDIS        │                      │
│  │  (in-process) │       │  CLUSTER      │                      │
│  │               │       │               │                      │
│  │  Hot keys     │       │  Source of    │                      │
│  │  (last 1ms)   │       │  truth for    │                      │
│  │  Approximate  │       │  counters     │                      │
│  │  check        │       │               │                      │
│  └──────────────┘       └──────────────┘                       │
│                                                                 │
│  ┌──────────────────────────────────────┐                      │
│  │       CONFIG SERVICE                  │                      │
│  │                                       │                      │
│  │  Rate limit rules:                    │                      │
│  │  { tenant_id: "acme",                 │                      │
│  │    rules: [                            │                      │
│  │      { endpoint: "/search",            │                      │
│  │        limit: 10, window: "second" },  │                      │
│  │      { endpoint: "/checkout",          │                      │
│  │        limit: 2, window: "minute" }    │                      │
│  │    ]                                   │                      │
│  │  }                                     │                      │
│  │                                       │                      │
│  │  Stored in: PostgreSQL                 │                      │
│  │  Cached in: Redis + local              │                      │
│  └──────────────────────────────────────┘                      │
└──────────────────────────────────────────────────────────────────┘
```

---

<a id="components"></a>
## 4. Component Selection

| Component | Choice | Why | Alternatives |
|-----------|--------|-----|-------------|
| **Counter Store** | Redis Cluster | Atomic INCR, sub-ms latency, supports Lua scripts for complex algorithms | Memcached (no atomic operations beyond INCR — can't do sliding window), DynamoDB (higher latency) |
| **Rate Check Location** | In-process (SDK/library) | Zero network latency for cached checks. Redis call only for misses | Sidecar proxy (adds 1ms latency per check), API gateway (single point of failure) |
| **Config Storage** | PostgreSQL → Redis cache | PostgreSQL for rules (ACID, easy to update), Redis for fast rule lookups at runtime | etcd (more complex, designed for config but overkill) |
| **Algorithm Execution** | Redis Lua Scripts | Atomic execution inside Redis (check + decrement in one atomic operation) | Application-level (race conditions between check and decrement) |

---

<a id="algorithms"></a>
## 5. Algorithms Deep Dive

### Algorithm 1: Fixed Window Counter

```
CONCEPT: Count requests in a fixed time window (e.g., 1 minute).

  Window: 10:00:00 to 10:00:59 (limit: 100 req/min)
  
  Request at 10:00:05 → count = 1 ✓
  Request at 10:00:10 → count = 2 ✓
  ...
  Request at 10:00:55 → count = 100 ✓
  Request at 10:00:56 → count = 101 ✗ REJECTED

  At 10:01:00 → new window, count resets to 0

Redis Implementation:
  key = "ratelimit:{user}:{endpoint}:{minute_bucket}"
  INCR key      → returns current count
  EXPIRE key 120 (2 min TTL for cleanup)

PROS: Simplest, fastest (one INCR), uses least memory
CONS: Burst at boundary — 100 requests at 10:00:59 + 100 at 10:01:00
      = 200 requests in 1 second. Edge burst problem.
```

### Algorithm 2: Sliding Window Log

```
CONCEPT: Keep a log of request timestamps. Count requests in the last N seconds.

  Limit: 100 req/min
  Current time: 10:00:30
  
  Log: [10:00:28, 10:00:25, 10:00:20, 10:00:15, 10:00:10, ...]
  Count timestamps in last 60 seconds (since 09:59:30): 95
  → Request allowed ✓
  → Add 10:00:30 to log

  New request at 10:00:31: count in last 60s = 96 → allowed ✓

Redis Implementation:
  key = "ratelimit:{user}:{endpoint}"
  ZADD key <timestamp> <unique_request_id>
  ZREMRANGEBYSCORE key 0 (current_time - 60000)  ← remove old entries
  ZCARD key  → count in window
  EXPIRE key 120

PROS: Most accurate — no boundary burst problem
CONS: Memory-intensive (stores every request timestamp), 
      slower (sorted set operations)
```

### Algorithm 3: Sliding Window Counter (RECOMMENDED)

```
CONCEPT: Approximate sliding window using two counters.

  Current minute count: C_current
  Previous minute count: C_previous
  
  Estimated count = C_previous × (1 - elapsed_fraction) + C_current
  
  Example:
    Limit: 100/min
    Current time: 10:00:30 (30 seconds into current minute)
    C_current (this minute so far): 40
    C_previous (last full minute): 80
    
    Estimated = 80 × (1 - 0.5) + 40 = 40 + 40 = 80
    → 80 < 100 → ALLOWED ✓

Redis Implementation (Lua script for atomicity):
  local current_key = KEYS[1]  -- current minute
  local prev_key = KEYS[2]     -- previous minute
  local limit = tonumber(ARGV[1])
  local now = tonumber(ARGV[2])
  
  local current_count = tonumber(redis.call('GET', current_key) or 0)
  local prev_count = tonumber(redis.call('GET', prev_key) or 0)
  local elapsed = (now % 60000) / 60000  -- fraction of minute elapsed
  
  local estimated = math.floor(prev_count * (1 - elapsed) + current_count)
  if estimated < limit then
      redis.call('INCR', current_key)
      return 1  -- allowed
  else
      return 0  -- denied
  end

PROS: Memory-efficient (2 counters per user/endpoint), 
      smooth (no boundary burst), accurate enough for most use cases
CONS: Slightly approximate (weighted average), requires 2 keys per limit
```

### Algorithm 4: Token Bucket

```
CONCEPT: Bucket holds N tokens. Each request consumes 1 token.
         Tokens refill at a fixed rate (e.g., 10 tokens/sec).

  Bucket capacity: 100 tokens
  Refill rate: 10 tokens/sec (1 token every 100ms)
  
  Request arrives:
    1. Refill: add tokens based on time since last refill
       tokens_to_add = (now - last_refill) × refill_rate
       tokens = min(capacity, tokens + tokens_to_add)
       last_refill = now
    2. If tokens >= 1: consume 1 token, ALLOW ✓
    3. If tokens < 1: DENY ✗

  Key advantage: ALLOWS BURSTS.
    User saves up 100 tokens (idle for 10 sec)
    Then makes 100 requests in 1 second → all allowed (burst)
    Then must wait for refill (10/sec) before more

Redis Implementation (Lua script):
  local key = KEYS[1]
  local capacity = tonumber(ARGV[1])
  local refill_rate = tonumber(ARGV[2])  -- tokens per second
  local now = tonumber(ARGV[3])
  
  local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
  local tokens = tonumber(bucket[1]) or capacity
  local last_refill = tonumber(bucket[2]) or now
  
  -- Refill
  local elapsed = now - last_refill
  tokens = math.min(capacity, tokens + elapsed * refill_rate)
  
  if tokens >= 1 then
      tokens = tokens - 1
      redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
      redis.call('EXPIRE', key, 3600)
      return {1, math.floor(tokens)}  -- allowed, remaining
  else
      redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
      redis.call('EXPIRE', key, 3600)
      return {0, 0}  -- denied
  end

PROS: Allows bursts (good for APIs where users make rapid requests),
      most flexible (rate + burst are independent),
      industry standard (AWS, Stripe, GitHub all use this)
CONS: Slightly more complex, requires 2 values per bucket (tokens + timestamp)
```

### Which Algorithm to Choose?

```
┌────────────────────┬──────────────┬──────────────┬──────────────────┐
│ Algorithm          │ Accuracy     │ Memory       │ Best For         │
├────────────────────┼──────────────┼──────────────┼──────────────────┤
│ Fixed Window       │ Low (burst)  │ Low (1 key)  │ Simple, rough    │
│ Sliding Window Log │ Exact        │ High (all ts)│ Strict compliance │
│ Sliding Window Ctr │ High (approx)│ Low (2 keys) │ Most use cases   │
│ Token Bucket       │ High         │ Low (2 vals) │ API rate limiting │
│                    │              │              │ (burst-friendly)  │
└────────────────────┴──────────────┴──────────────┴──────────────────┘

I USE: Token Bucket for API rate limiting (industry standard).
       Sliding Window Counter for strict compliance (financial transactions).
       Fixed Window for approximate, low-priority limits (analytics endpoints).
```

---

<a id="api"></a>
## 6. API Design

```yaml
# The rate limiter is a LIBRARY, not a separate service.
# It runs in-process inside each API server.

# Programmatic API (Python example):
from rate_limiter import RateLimiter

limiter = RateLimiter(redis_url="redis://cluster:6379")

# Check rate limit before processing request
@app.route("/api/search")
@limiter.limit(
    key=lambda: f"user:{current_user.id}:search",  # unique key per user+endpoint
    algorithm="token_bucket",
    capacity=10,       # burst of 10
    refill_rate=2      # 2 tokens/sec sustained
)
def search():
    return do_search()

# Response headers (returned to client):
# X-RateLimit-Limit: 10
# X-RateLimit-Remaining: 7
# X-RateLimit-Reset: 1690000060 (Unix timestamp when bucket refills)

# When rate limited → HTTP 429:
# HTTP/1.1 429 Too Many Requests
# Retry-After: 5
# X-RateLimit-Reset: 1690000060

# Admin API (for configuration):
POST /admin/v1/rate-limits
{
    "tenant_id": "acme-corp",
    "rules": [
        {
            "endpoint_pattern": "/api/search",
            "algorithm": "token_bucket",
            "capacity": 10,
            "refill_rate": 2
        },
        {
            "endpoint_pattern": "/api/checkout",
            "algorithm": "sliding_window",
            "limit": 2,
            "window_seconds": 60
        }
    ]
}
```

---

<a id="flow"></a>
## 7. Request Flow

```
User calls GET /api/search?q=shoes

Step 1: Request arrives at API server
  Load balancer → API Server Instance 3

Step 2: Rate limit middleware intercepts
  Before the request handler runs:

Step 3: Determine rate limit key
  key = "ratelimit:user:12345:search"

Step 4: Check local cache (in-process, last 10ms)
  LRU cache in process memory: { "ratelimit:user:12345:search": {tokens: 8, ts: ...} }
  → If found and recent (< 10ms old): use approximate check
  → If tokens > 0: decrement locally, ALLOW (proceed to handler)
  → This avoids Redis call entirely for high-frequency requests!

Step 5: If local cache miss → check Redis
  Execute Lua script atomically in Redis:
    - Refill tokens based on elapsed time
    - If tokens >= 1: consume, return {allowed: 1, remaining: 7}
    - If tokens < 1: return {allowed: 0, remaining: 0}

Step 6a: If allowed → proceed to request handler
  Response includes headers:
    X-RateLimit-Remaining: 7
    X-RateLimit-Reset: 1690000060

Step 6b: If denied → return 429 immediately
  HTTP/1.1 429 Too Many Requests
  Content-Type: application/json
  Retry-After: 5
  
  {
    "error": "rate_limit_exceeded",
    "message": "Too many requests. Retry after 5 seconds.",
    "retry_after": 5
  }
  
  Request handler is NEVER called. Saves server resources.

Step 7: Update local cache
  Store result in local LRU cache for next request
```

---

<a id="scaling"></a>
## 8. Scaling Strategy

```
BOTTLENECK 1: Redis throughput
  - 100K rate checks/sec
  - Single Redis: ~100K ops/sec (just barely)
  - Solution: Redis Cluster (3 shards) → 300K ops/sec headroom
  - Local in-process cache reduces Redis calls by 80%+ for hot users

BOTTLENECK 2: Redis network latency
  - Each Redis call: ~0.5ms network round trip
  - For 100K checks/sec across 10 API servers: ~10K calls/sec/server
  - Solution: Pipeline multiple checks, use local cache for hot keys
  - Connection pooling (reuse Redis connections)

BOTTLENECK 3: Memory usage
  - 5M keys × 100 bytes = 500 MB (comfortable for one Redis instance)
  - Active users (last hour) might be only 100K → 10 MB of hot data
  - TTL on all keys (auto-expire inactive users after 1 hour)

SCALING REDIS CLUSTER:
  - Shard by user_id hash → distributes counters across nodes
  - Each node handles ~1/3 of users
  - Add nodes as user count grows
```

---

<a id="failures"></a>
## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---------|--------|------------|
| **Redis down** | Can't check limits | FAIL OPEN (allow request, log error). Better to allow extra requests than block all users. Alternative: FAIL CLOSED (block all) for security-critical endpoints |
| **Redis slow** | Rate checks add latency | Timeout after 5ms. If timeout → use local cache (approximate). Circuit breaker: after 10 timeouts, skip Redis for 30 seconds |
| **Local cache corruption** | Inaccurate rate limiting | TTL on local cache (10ms). Self-heals quickly. Worst case: 1-2 extra requests per user |
| **Config service down** | Can't load new rules | Use cached rules from last successful fetch. Rules don't change frequently (days, not seconds) |
| **Redis Cluster partition** | Some shards unreachable | Consistent hashing routes to available shards. Failed shard's users: fail open |

**THE CRITICAL DECISION: FAIL OPEN vs FAIL CLOSED**

```
FAIL OPEN (Allow when Redis is down):
  → User experience: APIs work normally
  → Risk: Rate limits not enforced → potential abuse
  → Use for: Most APIs (better to allow abuse than block all users)

FAIL CLOSED (Deny when Redis is down):
  → User experience: All API calls fail (503 Service Unavailable)
  → Risk: No abuse possible
  → Use for: Security-critical (login attempts, payment endpoints)

My approach: Default FAIL OPEN, per-endpoint configurable to FAIL CLOSED.
```

---

<a id="tradeoffs"></a>
## 10. Trade-off Analysis

| Decision | Choice | Trade-off |
|----------|--------|-----------|
| **In-process vs Sidecar** | In-process (SDK) | Zero network latency, but every language needs its own SDK. Sidecar (Envoy) is language-agnostic but adds 1ms |
| **Fail open vs fail closed** | Fail open (default) | Better UX during outages, but potential for rate limit violations during Redis downtime |
| **Algorithm** | Token bucket (default) | Burst-friendly (good for API users), but slightly more complex than fixed window |
| **Local cache** | Yes (10ms TTL) | Reduces Redis load by 80%, but slightly inaccurate (a user could exceed by 1-2 req in the cache window) |
| **Lua scripts in Redis** | Yes | Atomic (check + decrement in one step), but adds complexity. Application-level check + decrement has race conditions |
| **Redis vs dedicated service** | Redis | Sub-ms latency, atomic operations. A dedicated rate-limit service (e.g., using Go + in-memory map) is faster but not distributed |
| **429 vs queueing** | 429 (reject) | Simpler — client retries with backoff. Queueing would add complexity and latency |

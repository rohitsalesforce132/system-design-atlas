# Architecture: URL Shortener (bit.ly / TinyURL)

> How to design a URL shortener that handles 100M redirects per day, generates unique short codes, tracks clicks, and serves redirects in under 50ms.

---

## Table of Contents

1. [Problem Statement & Requirements](#requirements)
2. [Capacity Estimation](#capacity)
3. [High-Level Architecture](#architecture)
4. [Component Selection](#components)
5. [Database Schema](#schema)
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
- Given a long URL, generate a short URL (e.g., bit.ly/abc123)
- Given a short URL, redirect to the original long URL
- Custom aliases (user chooses the short code)
- URL expiry (optional, user-configurable)
- Click analytics (total clicks, geographic, referrer, timestamp)
- API for programmatic shortening
```

### Non-Functional Requirements
```
- Read:Write ratio = 100:1 (far more redirects than shortenings)
- Redirect latency: < 50ms (99th percentile)
- Availability: 99.99% (redirects must always work)
- Short codes: 7 characters (62^7 = 3.5 trillion possibilities)
- Irreversible short codes (can't guess the next one)
```

---

<a id="capacity"></a>
## 2. Capacity Estimation

```
ASSUMPTIONS:
  - 100 million redirects/day (reads)
  - 1 million new shortens/day (writes)
  - Read:Write = 100:1

  QPS (Queries Per Second):
    Reads: 100M / 86400 = ~1,200 redirects/sec average
    Peak: ~5,000 redirects/sec (3x average)
    Writes: 1M / 86400 = ~12 shortens/sec

STORAGE:
  - Each URL record: ~500 bytes (short_code, long_url, user_id, timestamps, metadata)
  - 1M new records/day × 500 bytes = 500 MB/day
  - 5 years: 500MB × 365 × 5 = ~913 GB
  - Fits in a single PostgreSQL instance with headroom

BANDWIDTH:
  - Redirect response: ~500 bytes (HTTP 301 + Location header)
  - 100M redirects × 500 bytes = 50 GB/day outbound
  - ~5 MB/sec sustained

SHORT CODE GENERATION:
  - 7 characters from [a-zA-Z0-9] = 62 possible chars
  - 62^7 = 3,498,577,674,752 (~3.5 trillion URLs possible)
  - At 1M/day, we won't run out for 9.5 million years
```

---

<a id="architecture"></a>
## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      URL SHORTENER SYSTEM                         │
│                                                                  │
│                    ┌──────────────┐                              │
│         User ─────►│  DNS / CDN    │                             │
│                    │  (Cloudflare) │                             │
│                    └──────┬───────┘                              │
│                           │                                      │
│              ┌────────────┼────────────┐                         │
│              ▼            ▼            ▼                          │
│         ┌──────────────────────────────┐                         │
│         │     LOAD BALANCER (Nginx)     │                        │
│         └────────────┬─────────────────┘                         │
│                      │                                           │
│         ┌────────────▼─────────────────┐                         │
│         │      API SERVICE (Go)         │  ← Stateless            │
│         │                               │    Horizontally scalable│
│         │  POST /shorten  (write)       │                         │
│         │  GET /{code}     (redirect)   │                         │
│         │  GET /{code}/stats (analytics)│                         │
│         └──────┬─────────────┬─────────┘                         │
│                │             │                                    │
│         ┌──────▼──────┐ ┌───▼────────────┐                      │
│         │  REDIS      │ │  PostgreSQL     │                      │
│         │  (cache)    │ │  (source of     │                      │
│         │             │ │   truth)        │                      │
│         │ short_code  │ │                 │                      │
│         │  → long_url │ │ urls table      │                      │
│         │             │ │ clicks table    │                      │
│         │ TTL: 24h    │ │ (sharded later) │                      │
│         └─────────────┘ └─────────────────┘                      │
│                │                                                │
│         ┌──────▼──────────────────────┐                         │
│         │    KAFKA: click-events       │  ← Async analytics     │
│         └────────────┬─────────────────┘                         │
│                      │                                           │
│         ┌────────────▼─────────────────┐                         │
│         │   ANALYTICS WORKER            │  ← Consumes clicks     │
│         │   (aggregates: total, geo,    │    from Kafka          │
│         │    referrer, time)            │                         │
│         └────────────┬─────────────────┘                         │
│                      │                                           │
│         ┌────────────▼─────────────────┐                         │
│         │   CLICKHOUSE (analytics DB)   │  ← Billions of rows    │
│         └──────────────────────────────┘                         │
└──────────────────────────────────────────────────────────────────┘
```

---

<a id="components"></a>
## 4. Component Selection

| Component | Choice | Why | Alternatives |
|-----------|--------|-----|-------------|
| **API Service** | Go | High concurrency (goroutines), low memory, fast HTTP handling. Redirect = simple string lookup, Go excels | Node.js (good but more memory), Python (slower) |
| **Cache** | Redis | Sub-ms lookups. 100:1 read:write ratio means cache hit rate > 95%. 50M cached entries × 200 bytes = 10GB Redis | Memcached (no persistence — lose cache on restart) |
| **Primary DB** | PostgreSQL | ACID for URL records, B-tree index on short_code for O(log N) lookup. 913GB for 5 years fits in one instance | MySQL (similar), DynamoDB (overkill at this scale) |
| **Analytics DB** | ClickHouse | Columnar — billions of click events scanned in seconds for analytics dashboards | PostgreSQL (too slow for billions of rows), Elasticsearch (not optimized for counting) |
| **Message Queue** | Kafka | Click events are fire-and-forget. Kafka buffers spikes (viral URL → millions of clicks in minutes) | Redis Streams (simpler but lower throughput) |
| **CDN** | Cloudflare | Edge caching of redirects — user in Mumbai gets redirect from Mumbai edge, not Virginia | CloudFront (AWS-only) |

---

<a id="schema"></a>
## 5. Database Schema

```sql
-- URL mapping (source of truth)
CREATE TABLE urls (
    id BIGSERIAL PRIMARY KEY,
    short_code VARCHAR(10) UNIQUE NOT NULL,    -- "abc1234" (7 chars)
    long_url TEXT NOT NULL,
    user_id BIGINT,                            -- NULL = anonymous
    custom_alias BOOLEAN DEFAULT false,
    expires_at TIMESTAMPTZ,                    -- NULL = never expires
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT valid_short_code CHECK (short_code ~ '^[a-zA-Z0-9]{4,10}$')
);

CREATE INDEX idx_urls_short_code ON urls(short_code) WHERE is_active = true;
CREATE INDEX idx_urls_user ON urls(user_id, created_at DESC);
CREATE INDEX idx_urls_long_url ON urls(long_url);   -- prevent duplicates

-- Click tracking (for analytics — stored in ClickHouse, not PostgreSQL)
-- In ClickHouse:
CREATE TABLE clicks (
    short_code String,
    timestamp DateTime,
    ip_address String,
    country String,          -- geo-resolved from IP
    city String,
    referrer String,         -- where the click came from
    user_agent String,
    device String            -- mobile/desktop/tablet
) ENGINE = MergeTree()
ORDER BY (short_code, timestamp);
```

---

<a id="schema"></a>
## 5a. Short Code Generation Strategy

```
APPROACH 1: Base62 Encoding of Auto-Increment ID (RECOMMENDED)
  - PostgreSQL auto-increments id: 1234567890
  - Convert to Base62: [a-zA-Z0-9]
  
  1234567890 in Base62:
    1234567890 / 62 = 19912354 remainder 22 → 'w' (index 22)
    19912354 / 62 = 321167 remainder 40 → 'O' (index 40)
    ...
    Result: "dxOw" → pad to 7 chars: "000dxOw" → "AAAAdxOw"

  Pros: No collisions (ID is unique), short codes are short
  Cons: Predictable (someone could enumerate). Fix: add a secret offset/shuffle.

APPROACH 2: Random 7-character String
  - Generate 7 random chars from [a-zA-Z0-9]
  - Check if exists in DB → collision? Retry
  
  Pros: Not predictable
  Cons: 1M codes → collision probability increases (birthday paradox)
        At 1M codes: ~0.014% collision chance per attempt → acceptable

APPROACH 3: Hash of Long URL (MD5 first 7 chars)
  - MD5("https://example.com/very/long/url") → take first 7 chars
  
  Pros: Same URL always gets same short code
  Cons: Hash collisions (different URLs, same first 7 chars of MD5)
        Fixed length means high collision rate

I CHOOSE Approach 1 (Base62 of auto-increment ID) with a random offset
to prevent enumeration:
  display_code = base62(id + SECRET_OFFSET)
```

---

<a id="api"></a>
## 6. API Design

```yaml
# Shorten a URL
POST /api/v1/shorten
Authorization: Bearer <token>
{
    "long_url": "https://www.example.com/very/long/path?param=value",
    "custom_alias": "mysale",           # optional
    "expires_at": "2024-12-31T23:59:59Z" # optional
}
Response: 200 OK
{
    "short_url": "https://s.io/abc1234",
    "long_url": "https://www.example.com/...",
    "created_at": "2024-07-26T10:00:00Z"
}

# Redirect (the hot path — 100:1 ratio)
GET /{short_code}
→ No auth required (public)
→ HTTP 301 (permanent redirect, browser caches it)
   Location: https://www.example.com/very/long/path

  301 vs 302:
    301 (Permanent): Browser caches redirect → fewer server hits → faster
    302 (Temporary): Browser always checks server → accurate analytics
    I use 302 for analytics accuracy (want to count every click)

# Analytics
GET /api/v1/{short_code}/stats
Response:
{
    "total_clicks": 1547823,
    "clicks_today": 3421,
    "top_countries": [{"country": "India", "clicks": 500000}, ...],
    "top_referrers": [{"source": "twitter.com", "clicks": 200000}, ...],
    "timeline": [{"date": "2024-07-25", "clicks": 3200}, ...]
}
```

---

<a id="flow"></a>
## 7. Request Flow — Redirect (The Hot Path)

```
User clicks https://s.io/abc1234

Step 1: DNS resolves to nearest CDN edge
  DNS → Cloudflare edge in Mumbai (user is in India)

Step 2: CDN edge checks cache
  Cloudflare edge has seen this URL before?
  → YES: Return 302 redirect immediately (5ms, no origin hit)
  → NO: Forward to origin server

Step 3: Load Balancer → API Service
  Nginx routes to one of 10 Go API instances

Step 4: API checks Redis cache
  GET short:abc1234
  → "https://www.example.com/very/long/path"

  Cache HIT (95%+ of the time):
    → Return 302 redirect with Location header
    → Total latency: ~2ms (Redis lookup) + ~1ms (response) = ~3ms

  Cache MISS (5% of the time):
    → Query PostgreSQL: SELECT long_url FROM urls WHERE short_code = 'abc1234'
    → Store in Redis: SET short:abc1234 "https://..." EX 86400 (24h TTL)
    → Return 302 redirect
    → Total latency: ~5ms (DB lookup) + ~1ms = ~6ms

Step 5: Fire analytics event (async, non-blocking)
  Push to Kafka: { short_code: "abc1234", timestamp: now(), ip: "1.2.3.4",
                   referrer: "twitter.com", user_agent: "..." }
  → Non-blocking: don't wait for Kafka ack before returning redirect
  → Worker consumes from Kafka, writes to ClickHouse

Total latency: 3-6ms per redirect (excluding network RTT)
```

---

<a id="scaling"></a>
## 8. Scaling Strategy

```
READ SCALING (the bottleneck — 100:1 read:write):
  1. Redis cache: 95%+ hit rate → only 5% hits PostgreSQL
  2. CDN edge caching: Cloudflare caches 302 redirects at edge
     → Popular URLs never hit our servers at all
  3. Add PostgreSQL read replicas if cache miss rate is high
  4. Each Go instance handles 10,000+ redirects/sec

WRITE SCALING (not a bottleneck — 12 writes/sec):
  1. Single PostgreSQL handles this easily
  2. At 10M+ writes/day, partition by month or shard by short_code hash

ANALYTICS SCALING:
  1. ClickHouse handles billions of rows (columnar, compressed)
  2. Kafka buffers click event spikes (viral URL = millions of clicks/hour)
  3. Workers aggregate in micro-batches (write every 1,000 events or 5 seconds)

GEOGRAPHIC SCALING:
  1. CDN serves 80%+ of redirects from edge (nearest to user)
  2. Multi-region PostgreSQL read replicas for cache misses
  3. Writes go to single primary (acceptable at 12 writes/sec)
```

---

<a id="failures"></a>
## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---------|--------|------------|
| **Redis down** | All reads hit PostgreSQL → slower (6ms vs 3ms) | Redis Sentinel (HA). Even if down, system works — just slower |
| **PostgreSQL down** | New shortens fail. Existing redirects work from Redis cache | Streaming replication to standby + automatic failover |
| **CDN down** | All traffic hits origin → higher latency | Multiple CDN providers (Cloudflare primary, CloudFront fallback) |
| **Kafka down** | Analytics lost (redirects still work!) | Acceptable — analytics is non-critical. Buffer locally, replay later |
| **API instance crash** | Other instances absorb load | Load balancer health checks remove dead instances. Kubernetes auto-restarts |
| **Viral URL spike** | 1M clicks in 1 hour on one URL | CDN caches it at edge. Redis handles the rest. ClickHouse absorbs analytics |

---

<a id="tradeoffs"></a>
## 10. Trade-off Analysis

| Decision | Choice | Trade-off |
|----------|--------|-----------|
| **301 vs 302 redirect** | 302 (temporary) | More server hits (browser re-checks every time) but accurate analytics — every click is counted |
| **Short code generation** | Base62 of auto-increment ID + offset | Sequential but not guessable (offset hides pattern). Alternatives add collision risk |
| **Cache TTL** | 24 hours | Long TTL = fewer DB hits but stale redirects if URL is updated/deactivated. Fix: invalidate cache on URL change |
| **Sync vs async analytics** | Async (Kafka) | Click events might be lost during failure, but redirect latency stays at 3ms (don't block on analytics) |
| **Single DB vs sharded** | Single (913GB for 5 years) | Simpler operationally. If growth exceeds 2TB, shard by short_code hash |
| **Custom aliases** | Allowed but checked for profanity | More complex (content moderation) but important for brand/enterprise users |
| **Analytics storage** | ClickHouse (not PostgreSQL) | Two databases to manage, but ClickHouse is 100x faster for analytics queries |

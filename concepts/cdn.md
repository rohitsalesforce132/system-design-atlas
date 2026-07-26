# CDN (Content Delivery Network)

## What It Is (Cloudflare Example)

A CDN is a network of servers placed around the world that store copies of your content. Instead of every user fetching content from your single origin server, they fetch it from the nearest CDN server.

```
WITHOUT CDN:
                     ┌──────────────────┐
User in Mumbai ──────────────►│  Origin Server    │
  (150ms round trip)        │  (Virginia, USA)   │
                            │  Serves everything  │
                            └──────────────────┘
  Slow: 150ms for every request

WITH CDN:
  User in Mumbai               ┌──────────────────┐
    │                      │  Origin Server    │
    ▼                      │  (Virginia, USA)   │
  ┌────────────────┐         └──────────────────┘
  │ Mumbai CDN Edge │◄──cache fill───
  │ (5ms response)   │
  └────────────────┘
  Fast: 5ms for cached content
```

## Why CDNs Exist

The speed of light is the limit. Data traveling through fiber between Virginia and Mumbai takes **~70ms one way**. That's 140ms round trip — before any processing. A user in Mumbai fetching a video from Virginia waits at least 140ms before data even starts arriving.

CDN edge servers solve this by storing content physically close to users.

```
Fiber latency by distance (one-way):

  Distance      Latency
  ───────────────────────
  Same city     ~1ms
  Same country  ~10-25ms
  Same continent ~25-50ms
  Cross-continent ~50-100ms
  Opposite side  ~100-150ms
```

## How a CDN Works (Step by Step)

```
Step 1: User requests content

  Browser ──DNS lookup──► "What's the IP for images.netflix.com?"

Step 2: DNS responds with nearest edge location

  DNS ──► "Use 203.0.113.50 (Mumbai CDN edge server)"

Step 3: Browser connects to edge server

  Browser ──► Mumbai CDN Edge

Step 4: Edge checks its cache

  Cache HIT? → Return content immediately (5ms)
  Cache MISS? → Fetch from origin server, cache it, return
```

```
Detailed Flow:

  User's Browser
       │
       ▼
  DNS Resolution (points to CDN)
       │
       ▼
  ┌────────────────────────────┐
  │   CDN EDGE SERVER (Mumbai)  │
  │                            │
  │   Is content in cache?     │
  │   ├─ YES → Return (fast)   │
  │   └─ NO → Fetch from origin│
  │           │                │
  │           ▼                │
  │    ┌──────────────┐        │
  │    │ Origin Server │        │
  │    │ (Virginia)    │        │
  │    │ Fetch, cache, │        │
  │    │ return        │        │
  │    └──────────────┘        │
  └────────────────────────────┘
       │
       ▼
  User gets content
```

## What Goes Through a CDN?

| Content Type | Why CDN | TTL (cache duration) |
|-------------|---------|---------------------|
| Images, CSS, JS | Static, doesn't change often | Hours to months |
| Video chunks (HLS/DASH) | Large files, many viewers | Hours |
| API responses | Reduces DB load | Seconds to minutes |
| HTML pages | Fast page loads | Seconds to minutes |
| Software downloads | Large files | Months |

## CDN Caching Strategies

### Strategy 1: Push (Origin pushes content to CDN)
```
Origin ──push──► CDN Edge 1
              ──push──► CDN Edge 2
              ──push──► CDN Edge 3

Content is pre-placed before users request it.
Best for: Content known to be popular (new movie drops, software updates).
```

### Strategy 2: Pull (CDN fetches on first request)
```
User ──► CDN Edge ──MISS──► Origin
                              │
User ──► CDN Edge ◄──content──┘
       (cached now)

Subsequent users:
User ──► CDN Edge ──HIT──► Return (fast)
```

Most CDNs use pull by default.

### Strategy 3: Origin Shield
```
User ──► CDN Edge ──MISS──► Origin Shield ──MISS──► Origin Server
                              │
                         (single cache layer)
```

**Origin Shield** is a single CDN cache between edge servers and your origin. Without it:
```
100 edge servers, all miss → 100 requests hit origin → origin overwhelmed
```

With origin shield:
```
100 edge servers → origin shield (1 cache miss) → 1 request to origin → origin happy
```

## Key CDN Concepts

### TTL (Time to Live)
How long content stays in cache. Higher TTL = better hit rate but stale data risk.
```
Static logo:      TTL = 1 month (rarely changes)
Product page:     TTL = 5 minutes (changes occasionally)
Breaking news:    TTL = 10 seconds (changes rapidly)
Stock price:      TTL = 0 (no cache, real-time)
```

### Cache Invalidation
How to force CDN to serve fresh content:
1. **Versioned URLs:** `logo.png?v=2` — changing version forces new fetch.
2. **Purge API:** Tell CDN to drop specific content from cache.
3. **Soft purge:** Mark as stale — serve old content while fetching new in background.

### Geo-Routing
```
DNS routes user to nearest edge:

  User in Tokyo → Tokyo edge → 3ms
  User in London → London edge → 4ms
  User in São Paulo → São Paulo edge → 5ms
  User in Mumbai → Mumbai edge → 5ms
```

## Video Streaming via CDN (Netflix/YouTube Model)

Video streaming is the heaviest CDN use case. Here's how Netflix does it:

```
1. Netflix encodes movie into multiple resolutions:
   4K (25Mbps), 1080p (8Mbps), 720p (4Mbps), 480p (1.5Mbps)

2. Each resolution is split into small segments (4-10 seconds):
   segment_001.ts (4 seconds of video)
   segment_002.ts (4 seconds of video)
   ...

3. Segments are pushed to CDN edge servers globally.

4. User starts watching:
   Browser requests segment_001.ts
   → CDN edge serves it
   Browser requests segment_002.ts
   → CDN edge serves it
   → Meanwhile, browser monitors bandwidth
   → If bandwidth drops, requests lower quality segments
   (This is adaptive bitrate streaming — ABR)
```

```
User's Device (Netflix App)
  │
  ├── Request segment 1 (1080p)
  │     └── CDN serves in 200ms
  │
  ├── Measure download speed: 20 Mbps → good, keep 1080p
  │
  ├── Request segment 2 (1080p)
  │     └── CDN serves in 200ms
  │
  ├── WiFi drops → speed now 2 Mbps
  │
  ├── Request segment 3 (480p)  ← adaptive downgrade
  │     ──┘
  │
  └── WiFi recovers → speed back to 20 Mbps → request 1080p again
```

## Real-World CDN Examples

| Company | CDN Stack |
|---------|----------|
| **Netflix** | Netflix Open Connect (their own CDN appliances in ISP data centers worldwide) |
| **YouTube** | Google CDN (Google Front Ends in 100+ countries) |
| **Amazon** | CloudFront (AWS CDN) + S3 origins |
| **Facebook** | Akamai + their own edge cache (Facebook Edge Network) |
Netflix** | **owned CDN** (hardware they control) |

## CDN vs Edge Computing

Traditional CDN: Just caches content (dumb cache).
Edge Computing: Runs code at the edge (smart cache).

```
Traditional CDN Edge:
  User ──► "Give me /api/products" ──► Cache lookup ──► Return

Edge Computing (Cloudflare Workers, AWS Lambda@Edge):
  User ──► "Give me /api/products" ──► Run JavaScript at edge
                                      ├── Personalize for user's location
                                      ├── A/B test
                                      ├── Rate limit
                                      └── Return response
```

Edge computing can:
- Personalize content (show prices in user's currency)
- A/B test different versions
- Run auth checks (validate JWT token at edge)
- Redirect based on geolocation
- Compress responses
- Inject security headers

## How YOU Can Build This

### Level 1: Cloudflare Free Tier
```
1. Put Cloudflare in front of your server
2. Cloudflare auto-caches static assets
3. Free tier covers small sites
```

### Level 2: CloudFront (AWS)
```
CloudFront Distribution
  ├── Origins: S3 bucket (static) + Load Balancer (dynamic)
  ├── Behaviors:
  │   /static/* → cache for 1 day (S3 origin)
  │   /api/*    → no cache (LB origin)
  │   /images/* → cache for 1 hour (S3 origin)
  └── Edge locations: global auto-coverage
```

### Level 1: Nginx as a Mini-CDN
```
If you have servers in multiple regions:
  Nginx in Mumbai (proxy_cache) → caches API responses
  Nginx in Virginia (proxy_cache) → caches API responses

Users route to nearest Nginx via GeoDNS.
```

## Common Interview Questions

**Q: Why do CDNs exist if servers are already fast?**
A: Physics. Speed of light through fiber means cross-continent requests take 100ms+ minimum. CDN moves content closer to users, reducing latency to single-digit milliseconds. It also reduces load on origin servers.

**Q: How do you cache dynamic content?**
A: Three ways:
1. **Short TTL caching:** Cache API responses for 5-10 seconds. Good for popular products during traffic spikes.
2. **Edge computing:** Run logic at the edge (Cloudflare Workers) to generate personalized responses without hitting origin.
3. **Incremental Static Generation (ISG):** Rebuild static pages in background every N requests. Next.js does this.

**Q: What is adaptive bitrate streaming?**
A: The video player continuously monitors bandwidth and switches between resolutions. If bandwidth drops, it requests lower-quality segments to prevent buffering. If bandwidth increases, it requests higher quality. This is why Netflix never buffers on a good connection — it silently drops to 480p.

**Q: How does the CDN know when content changes? |
A: Three methods:
1. **TTL expiry:** Content expires after set time. Next request fetches fresh version.
2. **Versioned URLs:** `logo.png?v=2` changes the URL, forcing a new cache miss.
3. **Purge API:** You call an API to invalidate specific content. Effective immediately.
. |
4. **Origin shield:** Single cache layer reduces origin load.
```

**Q: What's a CDN hit rate and what's good? |
A: Hit rate = (cache hits) / (total requests). A good CDN hit rate is **90%+**. Below 60% means most requests reach origin, defeating the purpose.
```

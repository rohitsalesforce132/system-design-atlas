# Netflix — System Design Atlas

> **One-line summary:** Netflix is a globally distributed video streaming service that stores
> tens of thousands of hours of video in its own CDN (Open Connect) and pushes ~200 TB of video
> per second at peak through a microservices backend running on AWS.

---

## 1. Overview & Scale Numbers

Netflix started as a DVD-by-mail service in 1997 and pivoted to streaming in 2007. The product
you see today is the result of a 15-year journey from a monolith in a data center to a fully
cloud-native, microservices-based system that delivers a personalized movie to your screen within
two seconds of you pressing play.

### The numbers that shape every design decision

| Metric                                       | Approximate value        | Why it matters                                          |
| -------------------------------------------- | ------------------------ | ------------------------------------------------------- |
| Subscribers                                  | ~280M paid (2024)        | Drives global multi-region deployments                  |
| Concurrent streams at peak                   | ~25M+                    | Any bottleneck becomes visible                          |
| Peak aggregate bandwidth                     | ~200 Tbps                | CDN is mandatory — origin servers could never serve this |
| Catalog size                                 | ~17,000+ titles          | Petabytes of storage across many encodings             |
| Countries served                             | 190+                     | Needs regional licensing, language, and content rules  |
| Languages dubbed / subtitled                 | 30+                      | Adds encoding and asset complexity                      |
| New titles / metadata changes per day        | thousands                | Event-driven content pipeline                           |
| Average microservices                        | ~700+                    | Decoupled teams, independent deploys                    |
| Data per hour of 4K video                    | ~7 GB                    | Multi-bitrate ladder is core to cost                    |

### The real product goal

A user opens the app, sees a personalized home page in under a second, scrolls, presses Play, and
video starts within ~2 seconds, then never buffers for the next two hours. Everything else —
recommendations, billing, subtitles, watch history, resume position — is built around protecting
that core experience.

---

## 2. High-Level Architecture

Netflix's architecture splits cleanly into two halves:

1. **The backend control plane** — recommendations, billing, metadata, user state — runs on AWS.
2. **The media plane** — the actual video bytes — runs on Netflix's own global CDN called
   **Open Connect** (OCA appliances inside ISP data centers).

This split is the single most important design choice. The control plane is "stateful but slow"
(think: a database write). The media plane is "stateless but fast" (think: a static file on a
server near you). Separating them lets each scale independently.

```
                    ┌──────────────────────────────────────────────┐
                    │                  USER DEVICES                 │
                    │   TV  |  Mobile  |  Web  |  Tablet  | STB     │
                    └──────────────────────────┬───────────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       │            (1) API calls (JSON)                │
                       │            (2) Video chunks (HTTPS)            │
                       ▼                                               ▼
   ┌────────────────────────────────────┐               ┌──────────────────────────────┐
   │       CONTROL PLANE  (AWS)         │               │    MEDIA PLANE (Open Connect)│
   │                                    │               │                              │
   │  ┌──────────┐   ┌──────────────┐   │   steering    │   ┌──────────────────────┐   │
   │  │  Edge /  │   │ Recommender  │   │ ◀───────────▶ │   │  OCA Appliance (ISP)  │   │
   │  │  Zuul GW │   │   Service    │   │   manifest    │   │  caches encoded video │   │
   │  └────┬─────┘   └──────────────┘   │               │   └──────────────────────┘   │
   │       │                            │               │                              │
   │  ┌────▼──────────────────────────┐ │               │   ┌──────────────────────┐   │
   │  │   Microservices (~700)        │ │               │   │  Origin (AWS S3)      │   │
   │  │  billing, playback, search,   │ │ ──fill miss──▶│   │  master encodings     │   │
   │  │  subtitles, watch history ... │ │               │   └──────────────────────┘   │
   │  └───────────────────────────────┘ │               │                              │
   └────────────────────────────────────┘               └──────────────────────────────┘
```

### The "three legs" mental model

When you press Play, three things happen in parallel:

```
                press PLAY
                   │
        ┌──────────┼───────────┐
        ▼          ▼           ▼
   (a) license   (b) pick    (c) ask OCA
       check         best       for video
       (backend)     bitrate    chunks (CDN)
                    (backend)
```

- **(a)** is a backend call: "is this account allowed to watch this title in this region?"
- **(b)** is a backend call that returns a **manifest** — a list of all available video/audio/
  subtitle tracks at different bitrates, plus the URLs of the OCA appliances that have them.
- **(c)** is the device hitting the OCA over HTTPS and downloading video in small chunks.

The trick is: (c) should never go back through the AWS backend. Once the manifest is delivered,
all the heavy lifting — the 200 Tbps of video — is handled by Open Connect, not by microservices.

---

## 3. Detailed Component Breakdown

### 3.1 The API Gateway: Zuul

Zuul is the front door. Every request from a device hits Zuul first. It does:

- **TLS termination** (offloads crypto from backend services)
- **Authentication** — validates the Netflix session token
- **Routing** — `/api/home` → home page service, `/api/playback` → playback service, etc.
- **Rate limiting** — protects against abuse and thundering-herd incidents
- **Circuit breaking** — if a downstream service is failing, Zuul fails fast instead of queuing
  requests

Zuul is itself scaled horizontally behind an AWS Network Load Balancer.

### 3.2 Microservices

Netflix runs ~700 microservices. The most important ones for the user journey:

| Service                | Responsibility                                              |
| ---------------------- | ---------------------------------------------------------- |
| **Edge API**           | Aggregates responses from many services into one payload    |
| **Recommendation**     | Ranks titles for the home page; runs ML models             |
| **Playback**           | Issues the playback license, builds the manifest            |
| **Subtitle / Dubbing** | Knows which audio/subtitle tracks exist for a title         |
| **Watch History**      | Stores resume position, watched titles, completion %        |
| **Billing**            | Stripe-like subscription charges, dunning, plan changes     |
| **Search**             | Title and cast search; backed by Elasticsearch              |
| **ABTest**             | Decides which UI / recommendation variant a user sees       |
| **Identity**           | Account, profiles, parental controls                        |

Each service owns its own database (no shared tables), communicates via HTTP/gRPC or async events
over Apache Kafka, and can be deployed independently. This is the textbook **"database per
service"** pattern from the *Database-per-Service Microservices* book.

### 3.3 Storage layer

- **Cassandra** — the workhorse. Stores watch history, playback progress, user state. Cassandra
  was chosen because it is multi-region by design, handles huge write volumes, and has tunable
  consistency.
- **EVCache** — Netflix's memcached-based caching layer. Sits in front of Cassandra for reads.
  Most home-page loads never hit Cassandra.
- **MySQL (RDS)** — for transactional data that needs strong consistency (billing, account
  creation). Sharded horizontally.
- **S3** — for the master video files, encoded renditions, and large blobs.
- **Elasticsearch** — for search and some logging/analytics.
- **Kafka** — the async nervous system. Every interesting event ("user pressed play", "video
  buffered", "billing succeeded") goes to Kafka.

### 3.4 The media plane: Open Connect (OCA)

Open Connect is Netflix's **own CDN**. This is unusual — most companies use Akamai or Cloudflare.
Netflix chose to build their own because:

1. Video is 95%+ of their bandwidth, so they control cost only if they control the network edge.
2. They can co-locate appliances directly inside ISP facilities (called "embedded OCAs"), making
   the last mile shorter and cheaper.

An **OCA** is a custom 1U/2U server stuffed with SSDs and running a custom storage + HTTP server.
It sits inside the ISP's network. When a user requests a video chunk, DNS resolves to a nearby OCA
instead of to AWS.

```
   ISP facility A          ISP facility B          ISP facility C
   ┌──────────────┐         ┌──────────────┐        ┌──────────────┐
   │  OCA  (SSD)  │         │  OCA  (SSD)  │        │  OCA  (SSD)  │
   │   ~100 TB    │         │   ~100 TB    │        │   ~100 TB    │
   └──────┬───────┘         └──────┬───────┘        └──────┬───────┘
          │                        │                       │
          └──── requests from nearby users ────────────────┘
                                  │
                       (cache miss → fetch from origin S3)
```

### 3.5 The encoding pipeline

A single movie arrives from the studio as a ~50 GB mezzanine file. Netflix's encoding farm turns
it into a **bitrate ladder** of ~10+ renditions:

```
   one mezzanine (master)
            │
            ▼
   ┌────────────────────────────────────────────┐
   │   Parallel encode workers (on AWS)         │
   │   4K HDR, 1080p, 720p, 480p, 360p, ...     │
   │   + per-title optimized bitrates           │
   │   + multiple audio tracks (dubs)           │
   │   + subtitles in 30+ languages             │
   └──────────────────────┬─────────────────────┘
                          ▼
              push renditions → S3 → OCAs (worldwide)
```

The per-title optimization is important: an action movie with lots of motion needs more bits than
a talking-head documentary at the same resolution. Netflix analyzes each title and builds a custom
bitrate ladder per title.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌─────────────┐         ┌─────────────┐         ┌──────────────┐
   │   Account   │1───────*│   Profile   │1───────*│  Viewing     │
   │             │         │             │         │  Activity    │
   │ - email     │         │ - name      │         │ - profile_id │
   │ - plan      │         │ - language   │         │ - title_id   │
   │ - region    │         │ - kids?     │         │ - resume_pos │
   └─────┬───────┘         └─────────────┘         │ - completed  │
         │                                          └──────────────┘
         │
         │
   ┌─────▼───────┐         ┌──────────────┐         ┌──────────────┐
   │   Title     │1───────*│   Asset      │1───────*│   Segment    │
   │             │         │              │         │ (video chunk)│
   │ - id        │         │ - rendition  │         │ - seq_num    │
   │ - name      │         │ - resolution │         │ - url (OCA)  │
   │ - license   │         │ - bitrate    │         │ - duration_s │
   └─────────────┘         │ - codec      │         └──────────────┘
                           └──────────────┘
```

### 4.2 Schema choices

- **Account / Billing** → MySQL (sharded). Strong consistency required for money.
- **Title / Asset / Segment metadata** → Cassandra + EVCache. Mostly read-heavy, eventually
  consistent is fine.
- **Viewing Activity** → Cassandra. Huge write volume (every 30s a "heartbeat" with resume
  position), eventually consistent is acceptable.
- **Recommendation features / model inputs** → S3 + Iceberg/Hive tables in a data warehouse.
- **License entitlement** → in-memory + MySQL fallback. Must be fast and authoritative.

### 4.3 Why Cassandra for viewing activity

The viewing activity workload is: **massive write volume, eventually consistent reads, and a
global user base.** Cassandra fits because:

- Writes are append-only to a memtable + commitlog — extremely fast.
- Data is partitioned by `user_id`, so all of one user's history lives on a known set of nodes.
- Multi-region replication is built in (Netflix runs Cassandra across 3 AWS regions).
- You can tune consistency per query: `LOCAL_QUORUM` for reads you care about, `ONE` for
  background sync.

---

## 5. Request Flow — Pressing "Play"

This is the core user action. Let's trace what happens when you press Play on a TV.

```
USER          ZUUL       PLAYBACK SVC    LICENSE SVC    CASSANDRA    OCA (CDN)
 │              │              │              │             │           │
 │─Press Play──▶│              │              │             │           │
 │              │─auth+route──▶│              │             │           │
 │              │              │─check plan,─▶│             │           │
 │              │              │ region,kids  │             │           │
 │              │              │              │─read entitle│           │
 │              │              │              │◀─entitlement│           │
 │              │              │◀──allow/deny─┤             │           │
 │              │              │                                          │
 │              │              │  build manifest:                         │
 │              │              │  - list of renditions (res/bitrate)      │
 │              │              │  - audio tracks (lang)                   │
 │              │              │  - subtitle tracks                       │
 │              │              │  - URLs pointing at OCA appliances       │
 │              │              │                                          │
 │              │◀─manifest────┤                                          │
 │◀─manifest────┤              │                                          │
 │              │                                                          │
 │  device picks best rendition based on bandwidth probe                  │
 │              │                                                          │
 │────────── request video chunk #1 ─────────────────────────────────────▶│
 │◀───────────── video bytes ──────────────────────────────────────────────│
 │              │                                                          │
 │   (repeat for each chunk, adaptive bitrate switches renditions)         │
 │              │                                                          │
 │─heartbeat────▶│─(every ~30s)▶ Watch History svc → Cassandra            │
 │              │                                                          │
```

**Step-by-step:**

1. **Press Play.** Device POSTs `/playback/start` with `title_id` and session token.
2. **Zuul** terminates TLS, validates the session token, routes to the Playback service.
3. **Playback service** calls the License service: *Is this profile allowed to watch this title
   in this region under this plan?* The License service reads entitlements (cached in EVCache,
   backed by MySQL).
4. **Playback builds the manifest.** It queries the Title/Asset catalog to find all available
   renditions, audio tracks, subtitles, and the list of OCA URLs that currently cache each
   rendition. This is a JSON document.
5. **Manifest returns to device.** Now the AWS backend is done for this stream — the rest is
   device ↔ OCA.
6. **Device requests the first video chunk.** It picks an initial rendition (often a low one to
   start fast) and requests the chunk from the nearest OCA via HTTPS.
7. **OCA serves the chunk** from local SSD. If it's a cache miss, the OCA pulls from S3 origin
   and caches it.
8. **Adaptive bitrate (ABR).** As the stream plays, the device measures throughput and switches
   between renditions in the manifest — this is why Netflix "sharpens" a few seconds in.
9. **Heartbeats.** Every ~30s the device sends resume position + buffering events to the Watch
   History service, which writes to Cassandra and emits a Kafka event for telemetry.
10. **Resume across devices.** Because resume position is in Cassandra keyed by `profile_id`, you
    can pause on the TV and resume on the phone.

---

## 6. Scaling Strategy

### 6.1 Push the bytes to the edge

The single biggest scaling lever is Open Connect. By moving video out of AWS and into ISP
datacenters, Netflix removes 200 Tbps of traffic from their origin. **If your CDN can't scale, you
scale your CDN.**

### 6.2 Stateful scaling with Cassandra

Cassandra scales linearly by adding nodes. Netflix runs it across 3+ regions with tunable
replication. The key pattern: **partition by the hottest key** (`user_id` for viewing activity),
so writes are spread evenly.

### 6.3 Caching everywhere

```
   request ──▶ EVCache (memcached) ──▶ Cassandra
                  (~1ms)                  (~5-10ms)
```

Most reads never hit the database. Netflix's EVCache clusters handle tens of millions of reads
per second.

### 6.4 Auto-scaling on AWS

Services run on EC2 instances inside auto-scaling groups. Netflix wrote **Atlas** (monitoring)
and **Kayenta** (canary analysis) to auto-scale and auto-rollback bad deployments. A typical
deploy: spin up the new version at 10% traffic, measure error rates, roll forward or back.

### 6.5 Multi-region active-active

Netflix runs three AWS regions in active-active mode. If `us-east-1` goes down, traffic fails
over to `us-west-2` and `eu-west-1`. This was proven during the famous 2017 Christmas Eve
outage — well, the *goal* is to never repeat that.

### 6.6 Chaos engineering

Netflix invented **Chaos Monkey** and the Simian Army — tools that randomly kill production
instances to prove the system survives. If a single instance failure breaks playback, the
architecture is wrong.

---

## 7. Tech Stack

| Layer                | Technology                                            |
| -------------------- | ----------------------------------------------------- |
| Cloud                | AWS (EC2, S3, RDS, DynamoDB, IAM)                     |
| CDN                  | Open Connect (custom appliances, custom software)     |
| API Gateway          | Zuul (custom, Netty-based)                            |
| Service framework    | Spring Boot, Hystrix (circuit breaker, now archived)  |
| Service discovery    | Eureka                                                |
| Load balancing       | Ribbon (client-side)                                  |
| Databases            | Cassandra, MySQL (RDS), EVCache (memcached)           |
| Streaming/queueing   | Apache Kafka                                          |
| Search               | Elasticsearch                                          |
| Data warehouse       | Hive → Iceberg on S3, Spark, Flink, Presto/Trino       |
| Orchestration        | Titan (custom), Spinnaker (deployment)                |
| Monitoring           | Atlas (metrics), VectorDB, Flink (real-time alerts)   |
| Encoding             | FFmpeg, custom per-title optimization                 |
| Client player        | Custom per device; uses adaptive bitrate (DASH/HLS)   |
| Languages            | Java (backend), Node.js (UI services), Python (ML)    |

---

## 8. How YOU Can Build a Simplified Version

You cannot build Netflix's scale, but you can build a working streaming app in a weekend. Here's
the minimal viable architecture:

### 8.1 Minimal components

```
   ┌─────────┐    /api/video/:id     ┌──────────────┐    S3      ┌─────────┐
   │ Browser │◀─────────────────────▶│  Node/Flask  │◀──────────▶│  S3/    │
   │  player │                      │   backend    │            │  local  │
   └─────────┘                      └──────────────┘            └─────────┘
       │
       │  <video src="https://s3.../movie.m3u8">
       │  (HLS playlist → .ts chunks)
       ▼
   [ CDN: Cloudflare in front of S3 ]
```

### 8.2 Step-by-step weekend build

1. **Get a video.** Download a sample `.mp4`.
2. **Encode with FFmpeg into HLS.** Run:
   ```bash
   ffmpeg -i movie.mp4 \
     -codec:v libx264 -codec:a aac \
     -hls_time 6 -hls_playlist_type vod \
     -f hls movie.m3u8
   ```
   This produces a `.m3u8` playlist and dozens of `.ts` chunk files.
3. **Upload chunks to S3** (or even a local `static/` folder).
4. **Front it with Cloudflare** (free CDN) — this is your mini Open Connect.
5. **Build a tiny backend** in Flask or Express that serves:
   - `/api/movies` → list of titles
   - `/api/movies/:id` → metadata + the URL of the `.m3u8`
6. **Frontend.** A simple HTML page with:
   ```html
   <video src="https://cdn.yourdomain.com/movie.m3u8" controls></video>
   <script src="https://cdn.jsdelivr.net/npm/hls.js"></script>
   <script>
     if (Hls.isSupported()) {
       const hls = new Hls();
       hls.loadSource('https://cdn.yourdomain.com/movie.m3u8');
       hls.attachMedia(document.getElementById('video'));
     }
   </script>
   ```
   `hls.js` plays HLS in browsers that don't support it natively.
7. **Add a database.** A SQLite or Postgres table `videos(id, title, m3u8_url, duration)` is
   enough.
8. **Add auth.** JWT in the backend, check it in middleware.
9. **Add resume position.** A `watch_progress(user_id, video_id, position_seconds)` table updated
   on pause/heartbeat.
10. **Add multiple bitrates.** Re-encode with `-filter_complex` to produce 1080p/720p/480p and a
    master `.m3u8` that references all of them. The player auto-switches.

### 8.3 What you'll learn by building this

- How HLS / DASH adaptive streaming actually works.
- Why a CDN matters (watch your server's bandwidth the moment multiple users connect).
- How a backend protects a static asset with auth (signed URLs).
- Why resume-position needs a database, not localStorage.

### 8.4 Cost for a weekend build

- Cloudflare free tier + S3 (~$0.023/GB) + a $5/month VPS = essentially free for a few friends.
- The real Netflix spends billions on bandwidth because of scale, not because the tech is
  exotic.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                       | Alternative considered          | Why Netflix chose it                                   |
| ---------------------------------------------- | ------------------------------- | ------------------------------------------------------ |
| **Build own CDN (Open Connect)**               | Use Akamai/Cloudflare           | Video is 95% of cost; owning edge = control over cost  |
| **Move video bytes out of AWS**                | Serve video from EC2/S3         | Egress from AWS at 200 Tbps would be astronomically expensive |
| **Microservices (~700)**                       | Monolith                        | Team autonomy, independent deploys, failure isolation   |
| **Cassandra for viewing activity**             | MySQL                           | Write volume + multi-region + tunable consistency      |
| **EVCache in front of Cassandra**              | Read directly from DB           | Most reads are cacheable; Cassandra still costs per read |
| **Per-title bitrate optimization**             | Fixed bitrate ladder            | Saves ~20% bandwidth with no quality loss              |
| **Adaptive bitrate (ABR) on the client**       | Server decides bitrate          | Client knows real-time bandwidth; server can't          |
| **Active-active multi-region**                 | Active-passive failover         | A region outage shouldn't take down the service         |
| **Chaos engineering in production**            | Test in staging only            | Staging can't reproduce prod failure modes              |
| **Event-driven (Kafka) for telemetry**         | Sync writes to a DB             | Decouples producers from analytics consumers            |

### The deepest trade-off

The deepest trade-off in Netflix's design is **separating control plane from media plane**. The
benefit is enormous: the AWS backend only ever handles small JSON requests, while Open Connect
handles the 200 Tbps of video. The cost is organizational complexity — you now have two
engineering cultures (cloud + network) and a physical supply chain (shipping appliances to ISPs).
Most companies would never justify this. Netflix does because video is their entire product.

---

## 10. Common Interview Questions

**Q1: How would you design Netflix?**
Start with the user goal (play in <2s, no buffering), then split into control plane (AWS) and
media plane (CDN). Explain the manifest, adaptive bitrate, and why the CDN is mandatory at scale.

**Q2: How does Netflix handle 200 Tbps of video?**
Open Connect — a custom CDN with appliances embedded in ISP data centers. The AWS backend never
touches the video bytes after encoding.

**Q3: Why Cassandra instead of MySQL for viewing history?**
Huge write volume, global users, eventually consistent reads are fine, and Cassandra's
partitioning by `user_id` spreads load. MySQL would shard painfully at this scale.

**Q4: How does adaptive bitrate work?**
The player downloads a few chunks, measures throughput, and switches between renditions listed in
the manifest. Starting low and ramping up gives fast start-up.

**Q5: How do you serve different content per region (licensing)?**
The License service checks the user's region against title-specific entitlements. The manifest
only includes titles the user is allowed to stream in their region.

**Q6: How do recommendations work at a system level?**
Offline batch jobs compute candidate sets per user using collaborative filtering + content-based
features. An online ranker re-ranks candidates based on context (time of day, device). Results
are cached in EVCache; the home page reads from cache.

**Q7: How do you ensure playback starts in under 2 seconds?**
- Encode a low-bitrate "first chunk" so the first bytes arrive fast.
- Pre-fill OCA caches for popular titles.
- Client starts at a low rendition and ramps up.
- Manifest is small and cached.

**Q8: Why build your own CDN?**
At 200 Tbps, third-party CDN egress is the dominant cost. Owning the edge lets Netflix control
cost, cache fill logic, and co-locate inside ISPs.

**Q9: What happens if an OCA fails?**
DNS health checks remove it from the pool; the client retries another OCA from the manifest's URL
list. The manifest always lists multiple OCAs per rendition.

**Q10: How do you handle a new viral title (e.g., a new season drop)?**
Pre-positioning: Netflix knows a title will be popular, so the encoding pipeline finishes early
and the cache-fill pipeline pushes renditions to OCAs worldwide *before* release. This is why a
new season of a hit show doesn't crash Netflix.

---

## Further reading

- Netflix TechBlog (medium.com/netflix-techblog) — primary source for most of the above.
- "Full-Loop Observability at Netflix" — Atlas + telemetry.
- "Per-Title Encode Optimization" — Netflix's own paper on per-title bitrate ladders.
- Open Connect: openconnect.netflix.com
- Spinnaker and the Simian Army — deployment + chaos engineering.

---

*Last updated: July 2026. Numbers are approximate and based on publicly disclosed figures; Netflix
does not publish exact live metrics.*

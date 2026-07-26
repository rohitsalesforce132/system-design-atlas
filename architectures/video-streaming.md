# Video Streaming Platform — Sample Architecture

> **Audience:** A developer who wants to design a Netflix/YouTube-style video platform from scratch. Plain English, real numbers, ASCII diagrams, basics-first — analogies before advanced concepts.

---

## 1. Problem Statement & Requirements

### 1.1 What are we building?

A video platform where creators upload videos, the system **transcodes** them into multiple quality levels, stores them, and **streams** them to viewers via adaptive bitrate over a CDN. Includes a **recommendation** engine to drive engagement. Think Netflix (SVOD), YouTube (UGC), or Hotstar (live + VOD).

**The analogy:** Imagine a library that turns raw manuscripts into printed books. A customer experience looks like:
1. An author drops off a **rough draft** (the original upload — maybe a 4K file).
2. The library's **print shop** produces multiple editions: hardcover, paperback, large-print, audiobook (the transcode ladders — 1080p, 720p, 480p, 360p).
3. Each edition is copied to **branch libraries across the country** (CDN edge locations).
4. When a reader asks for a book, the nearest branch hands them the edition that **matches their reading speed** (adaptive bitrate — slow connection gets the "large print" 360p, fast gets 1080p).
5. A **librarian recommends** the next book based on what the reader enjoyed (recommendation engine).

The hard parts: (a) transcoding is CPU-intensive and slow, (b) video files are enormous (storage + bandwidth dominate cost), (c) streaming must adapt to viewer bandwidth in real time, (d) recommendations must be personalized at scale.

### 1.2 Functional Requirements

| # | Requirement | Priority |
|---|---|---|
| F1 | Video upload (resumable, large files) | P0 |
| F2 | Transcoding into multiple resolutions / bitrates (ABR ladder) | P0 |
| F3 | Video player with adaptive bitrate streaming (HLS/DASH) | P0 |
| F4 | CDN delivery with low startup time (<2s time-to-first-frame) | P0 |
| F5 | Video metadata: title, description, tags, thumbnails | P0 |
| F6 | Search and browse by category/tag | P0 |
| F7 | Recommendations / "up next" | P0 |
| F8 | View count, like/dislike, comments | P1 |
| F9 | User watch history, continue-watching | P0 |
| F10 | Live streaming (optional — noted separately) | P1 |
| F11 | Subtitles / multiple audio tracks | P1 |
| F12 | DRM / content protection (for premium SVOD) | P1 |
| F13 | Creator analytics dashboard | P2 |

### 1.3 Non-Functional Requirements

| Attribute | Target | Why |
|---|---|---|
| Availability | 99.99% | Streaming is entertainment but downtime = churn |
| Startup latency (time-to-first-frame) | p95 < 2s | Slow start → abandonment |
| Rebuffering ratio | <1% of playtime | Buffering is the #1 complaint metric |
| Read:Write ratio | ~10,000:1 (views per upload) | Once uploaded, a video is streamed many times |
| Transcode turnaround | <10 min for a 10-min video | Creators expect fast publish |
| Storage durability | 11×9 (S3) | Video loss = permanent content loss |
| Scalability | 1M concurrent viewers, 10k concurrent uploads | Flash virality / premieres |
| Bandwidth | Petabytes/day egress | Bandwidth is the dominant cost line |

### 1.4 Out of scope (for this design)

- Full live-streaming pipeline (we note where it differs; a separate doc covers low-latency live).
- Deep ML model training for recommendations (we cover inference + feature pipeline).
- Ad insertion / monetization.
- Creator-side video editor.

---

## 2. Capacity Estimation

Designing for a **YouTube-mid-scale platform**: 50M MAU, ~5M videos, growing.

### 2.1 View traffic

| Metric | Assumption | Math | Result |
|---|---|---|---|
| MAU | — | — | 50M |
| DAU | 40% of MAU | 50M × 0.40 | 20M |
| Videos/user/day | 5 (avg 4 min each) | 20M × 5 | **100M views/day** |
| Concurrent viewers (peak) | avg view 4 min, spread over day | 100M × 4min / (16h×60min) × 3 (peak factor) | **~1.25M concurrent streams** |

### 2.2 Upload traffic

| Metric | Math | Result |
|---|---|---|
| New videos/day | 100k uploads/day (UGC assumption) | 100k |
| Avg upload size | 1 GB (1080p, 10 min) | — |
| Upload bandwidth | 100k × 1 GB / 86400s | **~1.16 GB/s (9.3 Gbps)** inbound |
| Transcode output size | 2.5× input (all renditions + thumbnails) | 2.5 GB per video |

### 2.3 Storage

| Data | Per-unit | Volume | Total |
|---|---|---|---|
| Original uploads (1 yr) | 1 GB | 100k × 365 = 36.5M videos | **36.5 PB** |
| Transcoded renditions | 2.5 GB per video | × 36.5M | **91 PB** |
| Thumbnails | 50 KB × 4 per video | — | 7 TB |
| Metadata | 5 KB per video | — | 0.18 TB |
| **Total video storage (1 yr)** | | | **~128 PB** |

After 5 years: ~640 PB. This is why storage cost (and tiering — moving old videos to colder storage) is a first-order concern.

### 2.4 Bandwidth (egress — the dominant cost)

| Source | Math | Bandwidth |
|---|---|---|
| Avg stream bitrate | 2 Mbps (mixed renditions) | — |
| Concurrent viewers | 1.25M | — |
| Peak egress | 1.25M × 2 Mbps | **~2.5 Tbps** |
| Daily egress | 100M views × 4 min × 60s × 2 Mbps / 8 | **~6 PB/day** |

At CDN pricing (~$0.02–0.05/GB), **6 PB/day ≈ $120k–$300k/day in egress**. Bandwidth optimization (cache hit ratio, bitrate adaptation, peer-assisted delivery) directly hits the bottom line.

### 2.5 Compute

| Workload | Profile | Estimate |
|---|---|---|
| Transcoding | CPU-bound; ~1 min video per CPU-min for 1080p ladder | 100k videos × 10 min × 10 CPU-min = 10M CPU-min/day → ~7,000 vCPU steady |
| Segmenting (HLS packaging) | Fast, I/O-bound | bundled with transcode |
| Thumbnail generation | Light | <500 vCPU |
| Recommendation inference | GPU-batched | ~10 GPUs (batched) |
| API/web services | Light | ~30 pods × 4 vCPU |

Transcoding is the dominant compute cost and scales with **upload rate**, not viewer count.

### 2.6 CDN cache hit ratio needed

To control egress cost, target **>90% cache hit ratio** at the CDN edge. The CDN serves segments; the origin (S3) only serves cache misses (cold videos, seek-to-rare-timestamps). Without this ratio, origin bandwidth and cost explode.

---

## 3. High-Level Architecture

```
                          ┌─────────────────────────────────────────────────┐
                          │               VIEWERS & CREATORS                 │
                          │   Web · iOS · Android · Smart TV · Casting       │
                          └─────────────────────────────────────────────────┘
                                │                              │
                                │ watch (read-heavy)           │ upload (write, rare)
                                ▼                              ▼
   ┌──────────────────────────────────────────┐   ┌──────────────────────────────────┐
   │            PLAYBACK PATH                 │   │           INGEST PATH             │
   │                                          │   │                                  │
   │  ┌─────────┐  DNS/GeoDNS                │   │  Creator uploads via resumable   │
   │  │ Player  │──────────┐                  │   │  multipart upload →              │
   │  └─────────┘          ▼                  │   │                                  │
   │                 ┌──────────┐             │   │     ┌───────────────┐            │
   │                 │   CDN    │ (HLS/DASH   │   │     │  Upload       │            │
   │                 │ (CloudFrnt│ segments)  │   │     │  Service      │            │
   │                 │ /Akamai) │             │   │     │  (pre-signed  │            │
   │                 └────┬─────┘             │   │     │   S3 URLs)    │            │
   │                      │ cache miss         │   │     └──────┬───────┘            │
   │                      ▼                    │   │            │ original → S3     │
   │                 ┌──────────┐             │   │            │ (raw bucket)      │
   │                 │   S3     │ (origin:    │   │            ▼                    │
   │                 │ (encoded │ transcoded  │   │     ┌───────────────┐            │
   │                 │ bucket)  │ segments)   │   │     │  Metadata     │            │
   │                 └──────────┘             │   │     │  Service →    │            │
   │                                          │   │     │  PostgreSQL   │            │
   │  Player gets manifest from API →          │   │     └──────┬───────┘            │
   │  then streams segments from CDN           │   │            │ video.uploaded    │
   │                                          │   │            ▼                    │
   └──────────────────────────────────────────┘   │     ┌───────────────┐            │
                                                  │     │    Kafka      │            │
                                                  │     │  (events)     │            │
                                                  │     └───┬───────┬───┘            │
                                                  │         │       │                │
                                                  │         ▼       ▼                │
                                                  │   ┌────────┐ ┌────────┐         │
                                                  │   │Transcode│ │Thumbn. │         │
                                                  │   │Workers │ │Worker  │         │
                                                  │   │(FFmpeg)│ │(FFmpeg)│         │
                                                  │   └───┬────┘ └────┬───┘         │
                                                  │       │           │              │
                                                  │       ▼           ▼              │
                                                  │   writes renditions + thumbs     │
                                                  │   to S3 (encoded bucket)         │
                                                  │   → publishes manifest           │
                                                  │   → updates metadata (ready)     │
                                                  └──────────────────────────────────┘

                          ┌─────────────────────────────────────────────────┐
                          │              CONTROL / METADATA PLANE            │
                          │                                                  │
                          │   API Gateway → Video Service → PostgreSQL       │
                          │                → Search Service → Elasticsearch  │
                          │                → Recommendation Service         │
                          │                       ▲                          │
                          │                       │ features                │
                          │               ┌───────────────┐                  │
                          │               │ Feature Store │  ← watch events  │
                          │               │ + ClickHouse  │    (Kafka)       │
                          │               └───────────────┘                  │
                          └─────────────────────────────────────────────────┘
```

### 3.1 The three planes

1. **Ingest plane** — upload + transcode pipeline. Write-rare, CPU-heavy, async. Driven by Kafka events.
2. **Playback plane** — CDN + origin. Read-massive, I/O-heavy, the bandwidth-dominated path.
3. **Control/metadata plane** — APIs, search, recommendations, analytics. Latency-sensitive, user-facing.

The key insight: **ingest and playback are decoupled.** A video is uploaded once, transcoded once, and streamed millions of times. Separating these planes means a viral playback spike never blocks uploads, and a transcode queue backlog never degrades playback.

---

## 4. Component Selection

### 4.1 Object storage — S3 (or GCS / Azure Blob)

**Why:** Infinite scale, 11×9 durability, cheap, integrates natively with CDN and transcode workers. We use two buckets: `raw` (originals, cold) and `encoded` (transcoded segments + manifests, hot).
**Alternatives:** self-hosted Ceph/MinIO (more ops, only worth it for cost control at extreme scale); block storage (too expensive for PBs).

### 4.2 CDN — CloudFront / Akamai / Cloudflare

**Why:** Video is the canonical CDN use case. 90%+ of bytes must be served from the edge. CDN handles TLS, geographic caching, range requests. CloudFront integrates with S3 origin; Akamai has the best global footprint for video; Cloudflare is cost-effective.
**Alternatives:** self-hosted edge (cost-prohibitive); no CDN (origin bandwidth would be 10–100× more expensive and latency too high).

### 4.3 Transcoding — FFmpeg on autoscaled workers (or AWS MediaConvert)

**Why:** FFmpeg is the industry-standard transcoding tool (free, battle-tested, supports HLS/DASH packaging, every codec). We run it on autoscaled workers (Kubernetes jobs or spot instances) that consume from a Kafka queue. Each worker transcodes one video into a ladder of renditions (360p, 480p, 720p, 1080p) and packages into HLS segments.
**Alternatives:**
- *AWS MediaConvert / Elemental* — managed, zero ops, but per-minute pricing adds up at scale.
- *GPU-accelerated (NVENC)* — 5–10× faster transcoding for the same cost; worth it once CPU costs dominate.
- *FFmpeg with segmented parallelism* — split a video into chunks, transcode in parallel across workers, reassemble. Cuts turnaround for long videos.

### 4.4 Event bus — Kafka

**Why:** Decouples upload from transcode, thumbnail, metadata indexing, and analytics. Producers don't wait for consumers. Replay for re-processing (e.g., when we add a new rendition profile).
**Alternatives:** SQS (simpler, no replay); RabbitMQ (good for work queues, weaker for event replay).

### 4.5 Metadata DB — PostgreSQL

**Why:** Video metadata (title, description, tags, duration, status) is relational and benefits from ACID + rich queries. The metadata plane is read-heavy but low-volume compared to the video bytes.
**Alternatives:** DynamoDB (works, but ad-hoc queries harder); MongoDB (flexible schema, but we prefer Postgres for consistency with the rest of the atlas).

### 4.6 Search — Elasticsearch

**Why:** Full-text search over titles/descriptions/tags with ranking, facets (category, duration, upload date), and typo tolerance. Same rationale as the e-commerce architecture.
**Alternatives:** Postgres FTS (works at small scale, lacks facets); Algolia (SaaS, excellent, expensive at scale).

### 4.7 Analytics / feature store — ClickHouse + Redis

**Why:** ClickHouse is an OLAP columnar DB that ingests billions of view events from Kafka and answers "what did user X watch?", "what's trending?", and "completion rate per video?" in milliseconds. Redis serves as a low-latency feature store for the recommendation model (user's recent watch vectors, video embeddings).
**Alternatives:** BigQuery/Snowflake (managed OLAP, higher per-query cost); Spark (batch-oriented, higher latency).

### 4.8 Recommendation — two-tower neural model + heuristic fallback

**Why:** Embed users and videos into a shared vector space; retrieve top-N candidates by nearest-neighbor (ANN) search; rank with a richer model. Heuristic fallback (popular, continue-watching, same-channel) covers cold-start.
**Alternatives:** collaborative filtering (simpler, weaker for new content); pure popularity (cheap, low quality); no recs (unacceptable for engagement).

### 4.9 Streaming protocol — HLS (HTTP Live Streaming)

**Why:** HLS segments video into ~6-second `.ts` (or fMP4) chunks with a `.m3u8` manifest. The player fetches the manifest, then requests segments, switching renditions based on measured bandwidth (adaptive bitrate). HLS is universally supported (iOS native, web via hls.js, Android ExoPlayer, smart TVs).
**Alternatives:**
- *MPEG-DASH* — similar concept, slightly better codec support, weaker native iOS support.
- *Smooth Streaming* — Microsoft legacy, declining.
- *WebRTC* — only for sub-second live; overkill and expensive for VOD.

### 4.10 Upload — resumable multipart via pre-signed S3 URLs

**Why:** Uploads are large (1GB+) and networks are flaky. Multipart upload lets the client retry individual parts; pre-signed URLs mean the upload service never proxies bytes (saving its bandwidth and CPU).

---

## 5. Database Schema Design

### 5.1 Video metadata (PostgreSQL — `video` db)

```sql
CREATE TABLE videos (
    id              bigint PRIMARY KEY,
    creator_id      bigint NOT NULL,
    title           varchar(256) NOT NULL,
    description     text,
    tags            text[],
    category_id     bigint,
    status          varchar(16) NOT NULL,  -- uploading|processing|ready|failed|deleted
    duration_sec    int,
    original_s3_key varchar(256),          -- raw bucket
    manifest_s3_key varchar(256),          -- .m3u8 in encoded bucket
    thumbnail_refs  text[],                -- CDN URLs
    view_count      bigint DEFAULT 0,
    like_count      bigint DEFAULT 0,
    dislike_count   bigint DEFAULT 0,
    privacy         varchar(16) DEFAULT 'public',
    created_at      timestamptz DEFAULT now(),
    published_at    timestamptz
);
CREATE INDEX idx_videos_creator  ON videos(creator_id, created_at DESC);
CREATE INDEX idx_videos_category ON videos(category_id) WHERE status='ready';
CREATE INDEX idx_videos_tags_gin ON videos USING gin (tags);

CREATE TABLE video_renditions (
    id            bigint PRIMARY KEY,
    video_id      bigint REFERENCES videos(id),
    label         varchar(16),            -- "360p", "720p", "1080p"
    width         int,
    height        int,
    bitrate_kbps  int,
    codec         varchar(16),            -- h264, h265, vp9, av1
    manifest_key  varchar(256),           -- per-rendition .m3u8
    segment_count int,
    created_at    timestamptz DEFAULT now()
);
CREATE INDEX idx_renditions_video ON video_renditions(video_id);

CREATE TABLE subtitles (
    id          bigint PRIMARY KEY,
    video_id    bigint REFERENCES videos(id),
    language    char(5),                  -- en-US, hi, es
    format      varchar(8),               -- srt, vtt
    s3_key      varchar(256)
);
```

**Design note:** `status` drives the publish flow. A video is only streamable when `status='ready'` (transcode complete + manifest written).

### 5.2 User / creator (PostgreSQL — `users` db)

```sql
CREATE TABLE users (
    id            bigint PRIMARY KEY,
    email         varchar(128) UNIQUE,
    display_name  varchar(128),
    avatar_ref    varchar(256),
    is_creator    boolean DEFAULT false,
    subscriber_count bigint DEFAULT 0,
    created_at    timestamptz DEFAULT now()
);

CREATE TABLE subscriptions (
    subscriber_id bigint REFERENCES users(id),
    creator_id    bigint REFERENCES users(id),
    created_at    timestamptz DEFAULT now(),
    PRIMARY KEY (subscriber_id, creator_id)
);
CREATE INDEX idx_subs_creator ON subscriptions(creator_id);
```

### 5.3 Watch events (ClickHouse — OLAP)

```sql
CREATE TABLE watch_events (
    event_id    UInt64,
    user_id     UInt64,
    video_id    UInt64,
    session_id  String,
    position_sec UInt32,          -- where in the video
    duration_watched_sec UInt32,
    rendition   String,           -- which quality
    device_type String,           -- web/ios/android/tv
    bandwidth_mbps Float32,
    rebuffer_count UInt32,
    event_time  DateTime,
    ts          DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (user_id, video_id, ts);
```

This table ingests every play/pause/seek/rebuffer event via Kafka → ClickHouse sink. It powers watch history, completion-rate analytics, and recommendation features.

### 5.4 Recommendations (vector index — FAISS / Vespa / Milvus)

```json
// video_embedding
{ "video_id": 123, "embedding": [0.12, -0.04, ...], "updated_at": "..." }

// user_embedding (recomputed periodically from recent watches)
{ "user_id": 456, "embedding": [0.08, 0.11, ...] }
```

ANN (approximate nearest neighbor) retrieval: given a user vector, find the top-1,000 candidate video vectors, then rank with a richer model (watch count, freshness, creator affinity).

### 5.5 Search index (Elasticsearch)

```json
{
  "video_id": 12345,
  "title": "How to design a distributed system",
  "description": "...",
  "tags": ["system-design", "distributed-systems"],
  "creator": "TechChannel",
  "category": "Education",
  "duration_sec": 1820,
  "view_count": 150000,
  "published_at": "2026-07-20T...",
  "thumbnail": "https://cdn.../thumb.jpg"
}
```

---

## 6. API Design

### 6.1 Upload (creator → platform)

```
POST /v1/videos/init-upload
  Body: { "title":"...", "description":"...", "filename":"vid.mp4", "size":1073741824 }
→ 200 OK
  {
    "video_id": 88001,
    "upload_url": "https://s3.../presigned?part=...",
    "upload_id": "multipart-abc",      // for resumable multipart
    "chunk_size": 5242880
  }

POST /v1/videos/{id}/complete-upload
  Body: { "upload_id":"multipart-abc", "parts":[{"part":1,"etag":"..."},...] }
→ 202 Accepted
  { "status":"processing", "estimated_ready_in_sec": 600 }
   // triggers Kafka event → transcode pipeline
```

### 6.2 Video metadata & playback

```
GET /v1/videos/{id}
→ 200 OK
  {
    "id":88001, "title":"...", "creator":{...}, "duration":1820,
    "thumbnails":[...], "view_count":150000,
    "manifest_url":"https://cdn.../88001/master.m3u8",   // HLS
    "renditions":[{"label":"1080p",...},{"label":"720p",...}],
    "status":"ready"
  }

GET /v1/videos/{id}/manifest  (or let CDN serve the .m3u8 directly)
→ returns HLS master playlist referencing rendition playlists
```

### 6.3 The HLS manifest (what the player actually fetches)

```
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
360p.m3u8
```

Each rendition playlist lists ~6-second `.ts` segments. The player measures download speed and switches renditions between segments — this is **adaptive bitrate streaming (ABR)**.

### 6.4 Search & recommendations

```
GET /v1/search?q=system+design&category=Education&sort=relevance
→ { "hits":[ {"video_id":..., "title":..., "thumbnail":..., "views":...} ], "total":120 }

GET /v1/recommendations?user_id=456&context=home
→ { "videos":[ ...top-N personalized...] }

GET /v1/recommendations/next?video_id=88001
→ { "videos":[ ...up-next after this video...] }
```

### 6.5 Engagement events (player → backend)

```
POST /v1/events/watch
  Body: { "video_id":88001, "session_id":"s_1", "event":"play"|"pause"|"seek"|"rebuffer",
          "position_sec":45, "rendition":"720p" }
→ 202 Accepted   // queued to Kafka → ClickHouse
```

These events stream continuously during playback and feed the feature store + analytics.

---

## 7. Step-by-Step Request Flow

### 7.1 Upload → Transcode → Publish

```
 Creator            Upload Svc     S3 (raw)   Metadata Svc    Kafka      Transcode     S3 (enc)   CDN
 ────────            ─────────     ────────   ────────────    ────      ──────────     ────────   ───
   │
   │── POST /init-upload ─▶│
   │                       │── create video record (status=uploading) ─▶│
   │◀── upload_url ────────│
   │
   │── PUT chunk1 ──────────────────────────▶│  (direct to S3 via pre-signed URL)
   │── PUT chunk2 ──────────────────────────▶│
   │   ...
   │── POST /complete-upload ─▶│
   │                       │── finalize multipart in S3 ─────────────────────────▶│
   │                       │── update status=processing ─▶│
   │                       │── produce video.uploaded ─────────────────▶│
   │◀── 202 (processing) ──│
   │
   │                       │                                    │── consume ─▶│
   │                       │                                    │              │── FFmpeg:
   │                       │                                    │              │   decode raw
   │                       │                                    │              │   for each rendition:
   │                       │                                    │              │     transcode + segment (6s .ts)
   │                       │                                    │              │     package .m3u8
   │                       │                                    │              │   generate thumbnails
   │                       │                                    │              │   (GPU-accelerated if available)
   │                       │                                    │              │── write renditions + manifests ─▶│
   │                       │                                    │              │── update status=ready ─▶│
   │                       │                                    │              │── index to Elasticsearch ─▶│
   │                       │                                    │              │── push to CDN origin ──────────▶│
   │                       │                                    │              │── produce video.ready ─▶│
   │
   │◀── push notification / email: "Your video is published!" ─────────────────────────────────────────│
```

### 7.2 Viewer playback (the hot path)

```
 Viewer             API Gateway    Video Svc     CDN           S3 (origin)
 ──────             ───────────    ─────────    ────          ────────────
   │
   │── click video ─▶│
   │                 │── GET /videos/{id} ──▶│
   │                 │◀── metadata + manifest_url ─│
   │◀── page + player ─│
   │
   │── player fetches master.m3u8 ──────────────────────▶│
   │◀── manifest (list of renditions) ────────────────────│
   │
   │── player picks 720p, fetches 720p.m3u8 ─────────────▶│
   │◀── segment list ─────────────────────────────────────│
   │
   │── GET segment_001.ts ────────────────────────────────▶│ (cache HIT)
   │◀── bytes ──────────────────────────────────────────────│
   │   player measures download speed:
   │     if fast → upgrade to 1080p for segment_002
   │     if slow → downgrade to 480p
   │── GET segment_002.ts (1080p) ────────────────────────▶│
   │◀── bytes ──────────────────────────────────────────────│
   │   ... continues, ABR switches as bandwidth fluctuates ...
   │
   │── POST /events/watch (every 10s) ─▶│ → Kafka → ClickHouse + feature store
   │
   │── near end → GET /recommendations/next ─▶│ → returns "up next" video
```

### 7.3 Why this flow achieves <2s time-to-first-frame

1. **Metadata fetch** is fast (CDN-cached page, Redis-cached metadata).
2. **Manifest fetch** is small (~1 KB) and CDN-cached.
3. **First segment** is the only large fetch; CDN edge cache makes it a local hit for popular videos.
4. **Player starts playback as soon as segment_001 arrives** — doesn't wait for the whole video.
5. **ABR starts conservative** (lower rendition) to get the first frame fast, then ramps up.

### 7.4 Sequence diagram: the adaptive bitrate decision

```
 Player                          Network
 ──────                          ───────
   │
   │── download segment_001 (720p, 6s video, took 3s to download)
   │   measured bandwidth: (6s × 2.5Mbps) / 3s = 5 Mbps available
   │   decision: 5 Mbps > 1080p threshold (4 Mbps) → UPGRADE
   │
   │── download segment_002 (1080p)
   │   ...network degrades, took 8s to download
   │   measured bandwidth: (6s × 5Mbps) / 8s = 3.75 Mbps
   │   decision: 3.75 < 4 Mbps threshold → DOWNGRADE to 720p
   │
   │── download segment_003 (720p)
   │   ...
```

The player maintains a sliding window of recent download times and switches renditions to keep the buffer healthy (target: 10–30s of buffered video).

---

## 8. Scaling Strategy

### 8.1 Playback (the bandwidth-dominated path)

| Bottleneck | Solution |
|---|---|
| Multi-Tbps egress | CDN absorbs 90%+; origin (S3) only sees cache misses |
| Origin bandwidth (cold videos) | Pre-warm CDN for new releases; longer TTLs; multi-tier caching |
| Startup latency | ABR starts low-rendition; segment prefetch; DNS/Connection reuse |
| Rebuffering | Larger buffer target; faster startup rendition; prefetch next segments |

### 8.2 Transcoding (the CPU-dominated path)

| Bottleneck | Solution |
|---|---|
| Sustained transcode load | Autoscaled worker pool consuming Kafka; scale by queue depth |
| Long videos blocking workers | **Chunked transcoding**: split video into N parts, transcode in parallel, concatenate |
| GPU cost | NVENC-accelerated workers (5–10× throughput per $); spot instances for batch |
| Transcode turnaround | Prioritize queue (creator rep / premiere); degrade old uploads to background |

### 8.3 Storage

| Concern | Solution |
|---|---|
| 100s of PB | S3 lifecycle policies: move originals to Glacier after transcode; tier encoded renditions by popularity |
| Hot vs cold videos | CDN + Redis cache manifests for top videos; cold videos fetched from S3 on demand |
| Dedup | Hash-based dedup of identical uploads (UGC reposts) |

### 8.4 Metadata & search

- PostgreSQL read replicas for `GET /videos/{id}`; Redis cache for hot videos.
- Elasticsearch scales horizontally by adding data nodes; index refreshed every few seconds from Kafka `video.ready` events.

### 8.5 Recommendations at scale

- **Candidate generation (retrieval):** ANN index (FAISS/Vespa) over video embeddings → top-1,000 candidates in <10 ms.
- **Ranking:** lightweight model (GBDT or small NN) re-ranks candidates using user features + context → top-20.
- **Feature freshness:** user vectors recomputed every few minutes from ClickHouse watch events (streaming).
- **Cold start:** new videos get a boost (exploration); heuristic fallback (popular, same-channel, continue-watching) when personalization is thin.

### 8.6 Event ingestion (ClickHouse)

- Kafka → ClickHouse sink (clickhouse-kafka connector or Vector).
- Batch inserts (every few seconds) to amortize write cost.
- Partition by month for efficient retention/drop.

### 8.7 Multi-region

- **Ingest regions:** creators upload to the nearest region (lower latency); transcoding can be regional or centralized (cheaper compute regions).
- **Playback regions:** CDN edges everywhere; origin in 2–3 regions for resilience.
- **Metadata:** single writer region + read replicas globally (eventual consistency acceptable for metadata).

---

## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---|---|---|
| **CDN edge failure** | Viewers in that region see slow streams / errors | CDN multi-edge failover; origin fallback (higher latency but functional); health checks |
| **S3 origin outage** | Cache misses fail; cold videos unavailable | Cross-region S3 replication; CDN serves stale (stale-while-revalidate) |
| **Transcode worker crash mid-job** | Video stuck in `processing` | Idempotent transcode (segment-level checkpoints); job re-queue on crash; dead-letter queue for poison messages |
| **Kafka lag** | Transcode backlog grows; uploads delayed | Auto-scale workers by queue depth; alert on consumer lag; priority queues for creator rep |
| **Manifest corruption** | Player can't parse → playback fails | Validate manifest before marking `ready`; fallback to a known-good rendition |
| **Player network degradation** | Frequent rebuffering | ABR downshifts; player caches ahead; graceful quality reduction (not failure) |
| **Recommendation model staleness** | Stale "up next" | Periodic retraining; real-time feature updates; heuristic fallback always available |
| **Upload interrupted** | Partial file | Resumable multipart upload; client retries only missing parts |
| **Viral spike on one video** | CDN cache stampede on cold segments | Pre-warm; request coalescing at CDN (multiple viewers → one origin fetch) |
| **Codec bug in FFmpeg** | Failed transcodes | Pin FFmpeg version; canary new versions; fallback rendition (at least 720p always produced first) |
| **Clock skew** | Analytics time errors | Server-side timestamping; NTP on hosts |

### 9.1 The "video is never lost" guarantee

- Original upload → S3 with 11×9 durability + versioning.
- Transcode is **idempotent and restartable**: workers checkpoint per-segment; a crash resumes from the last completed segment.
- A sweeper job detects `status=processing` videos older than threshold and re-queues them (recovering from silent worker deaths).

---

## 10. Trade-off Analysis

### 10.1 HLS (6s segments) vs. low-latency alternatives

- **Choice:** HLS with 6-second segments.
- **Why:** Universal player support (iOS native, web hls.js, Android, TVs); CDN-friendly (plain HTTP); simple packaging.
- **Cost:** 6s segments impose ~6–12s glass-to-glass latency for live. For VOD this is irrelevant; for live we'd switch to LL-HLS (2s segments) or WebRTC (sub-second, much more expensive).

### 10.2 Transcode-on-upload vs. transcode-on-demand

- **Choice:** Transcode all renditions on upload (eager).
- **Why:** Predictable quality at playback; no first-viewer latency penalty; simpler CDN caching (all segments exist immediately).
- **Cost:** Storage and compute for renditions that may never be watched (long-tail videos). Alternative: transcode only 720p eagerly, produce other renditions lazily on first request from each region. Saves storage but adds first-viewer latency and complexity. We choose eager for simplicity at our scale; at YouTube scale, per-video ROI analysis would drive a hybrid.

### 10.3 CDN vs. P2P (peer-assisted delivery)

- **Choice:** CDN-only (no P2P).
- **Why:** Simplicity, reliability, predictable QoE.
- **Cost:** Higher egress cost. P2P (WebRTC mesh, or solutions like Peer5) can cut origin bandwidth 30–50% for viral content by having viewers share segments among themselves. Worth it at extreme scale; adds client complexity and works poorly on restricted networks (corporate, mobile). We'd add P2P as an optimization layer once bandwidth cost justifies the engineering.

### 10.4 Two-tower recommendations vs. collaborative filtering

- **Choice:** Two-tower neural retrieval + ranking model.
- **Why:** Handles new content (content-based features), personalizes at scale, retrieves from millions of candidates in milliseconds.
- **Cost:** Requires ML infrastructure (training pipeline, feature store, serving). Collaborative filtering is cheaper but fails for cold-start (new videos, new users). At small scale, CF + popularity is fine; at our scale, the neural approach's engagement lift pays for itself.

### 10.5 ClickHouse vs. Spark/Batch analytics

- **Choice:** ClickHouse (real-time OLAP).
- **Why:** Sub-second queries on billions of events; powers both analytics dashboards and real-time recommendation features.
- **Cost:** Higher write-path complexity (Kafka sink); less flexible than Spark for ad-hoc heavy ML. We use Spark only for offline model training; ClickHouse for everything online.

### 10.6 Eager thumbnail generation vs. on-demand

- **Choice:** Generate 4 thumbnails per rendition at transcode time.
- **Why:** Thumbnails are on every search/browse/recommendation tile — they're read 100× more than the video itself. Generating lazily would make the first impression slow.
- **Cost:** Small compute + storage overhead. Worth it.

### 10.7 Single origin vs. multi-origin

- **Choice:** Multi-region S3 origins behind the CDN.
- **Why:** Resilience (one region outage doesn't kill playback); latency (origin nearer to each CDN edge).
- **Cost:** Cross-region replication cost + storage duplication. Mitigated by replicating only encoded renditions (not originals) and only for popular videos (tiered replication).

### 10.8 Managed transcode (MediaConvert) vs. self-managed FFmpeg

- **Choice:** Self-managed FFmpeg on autoscaled workers.
- **Why:** Cost control at scale (per-minute managed pricing dominates at our transcode volume); full codec/format control; ability to use GPU (NVENC) and chunked-parallel transcode.
- **Cost:** Operational burden (worker management, FFmpeg versioning, failure handling). At small scale, MediaConvert is the right choice (zero ops); the crossover is around thousands of transcode-hours/day.

---

## Appendix: Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| Upload | Pre-signed S3 multipart URLs | Resumable, offloads bytes from app servers |
| Object storage | S3 (raw + encoded buckets) | Infinite scale, durable, cheap |
| Event bus | Kafka | Decouples ingest from transcode/analytics |
| Transcoding | FFmpeg on autoscaled workers (GPU optional) | Industry standard, cost-controlled, parallelizable |
| Packaging | HLS (.m3u8 + .ts segments) | Universal player support, ABR |
| CDN | CloudFront / Akamai / Cloudflare | Edge caching, bandwidth offload |
| Metadata DB | PostgreSQL | Relational, ACID, rich queries |
| Search | Elasticsearch | Full-text, facets, ranking |
| Analytics | ClickHouse | Real-time OLAP on view events |
| Feature store | Redis | Low-latency features for recs |
| Recommendations | Two-tower NN + ANN (FAISS/Vespa) | Personalization at scale |
| Streaming protocol | HLS with ABR | Adaptive quality, universal support |
| Player | hls.js (web), AVPlayer (iOS), ExoPlayer (Android) | Native HLS support |
| Orchestration | Kubernetes (workers) + spot instances | Scale transcode elastically |
| Observability | Prometheus + Grafana + Jaeger | QoE metrics (startup, rebuffer) are the SLO |

---

*End of Video Streaming architecture.*

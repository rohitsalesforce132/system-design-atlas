# YouTube — System Design Atlas

> **Audience:** A developer transitioning to AI/ML engineering who wants to understand how YouTube is built end-to-end. Plain English, real numbers, ASCII diagrams, basics-first.

---

## 1. Overview & Scale Numbers

YouTube is the **world's largest video platform** — a search engine for video, a recommendation feed, a live-streaming platform, a Shorts (short-video) feed, and a music streaming service, all in one. It is the canonical system-design problem for **video at exabyte scale** and the original "how would you design YouTube" interview question.

### Scale (public numbers + estimates)

| Metric | Value | Why it matters |
|---|---|---|
| Monthly active users (MAU) | ~2.7+ billion | Largest video platform on Earth |
| Videos on platform | ~800+ million to billions | Catalog size drives search + rec systems |
| Hours of video uploaded per minute | ~500+ hours/minute | Ingest is relentless |
| Daily watch time | ~1+ billion hours/day | Staggering engagement |
| Videos served per second (peak) | millions of streams | Bandwidth dominates cost |
| Languages | 80+ (auto-translated captions) | Global reach |
| Data stored | exabytes (EB) | Video storage is one of the largest datasets on Earth |
| Recommendation model | multi-task DNN (two-tower + ranking) | Drives 70%+ of watch time |
| Data centers | Google global fleet | Free energy from Google infra |
| Engineers | thousands (within Google) | Part of Google engineering org |

### Why the numbers matter

YouTube's defining engineering challenge is **video at exabyte scale**: storing, transcoding, and serving video so cheaply that ad revenue covers it, while keeping search and recommendation latency under a few hundred milliseconds. Every architectural decision is shaped by the fact that **video blobs are enormous** and **the catalog grows by 500 hours/minute**. YouTube benefits massively from running on Google's infrastructure — Borg/Kubernetes, Colossus (GFS successor), Andromeda networking — which is a competitive moat no competitor can easily replicate.

### The one-paragraph summary

YouTube is a **video catalog + search engine + recommendation engine**. Creators upload video; the system transcodes it into many bitrate renditions, extracts metadata (thumbnails, captions, content tags), and indexes it for search and recommendation. Viewers search or browse; the system returns ranked results, serves video from a nearby CDN edge (Google's edge network), and logs every interaction to retrain the recommenders. The whole thing rides on Google's planet-scale infrastructure.

---

## 2. High-Level Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              USER DEVICES                                    │
 │       iOS · Android · Web · Smart TV · Chromecast · Game consoles            │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │  HTTPS / QUIC
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                          EDGE / CDN LAYER                                    │
 │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────────┐ │
 │  │ Google Edge  │  │ Video CDN    │  │  Edge caching (popular videos       │ │
 │  │ POP (GFE)    │  │ (segmented,  │  │   cached near users)                │ │
 │  │ TLS, auth    │  │  HLS/DASH)   │  │                                     │ │
 │  └──────────────┘  └──────────────┘  └─────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                       APP / SERVICE LAYER                                    │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ Upload       │  │ Transcoding  │  │ Search       │  │ Recommendation  │  │
 │  │ Service      │  │ Service      │  │ Service      │  │ Service         │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ Video        │  │ Comment /    │  │ Live         │  │ Shorts          │  │
 │  │ Metadata     │  │ Interaction  │  │ Streaming    │  │ Feed            │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                     ML / UNDERSTANDING LAYER                                 │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ Search       │  │ Rec          │  │ Content      │  │ Caption /       │  │
 │  │ Ranking      │  │ Ranking      │  │ Understanding│  │ Translation     │  │
 │  │ (DNN)        │  │ (DNN)        │  │ (CV + ASR)   │  │ (Speech → text) │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              STORAGE LAYER                                   │
 │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────┐  └──────────┐ │
 │  │ Colossus   │  │ Bigtable   │  │ Spanner     │  │ Memory  │  │ Event     │ │
 │  │ (video     │  │ (metadata, │  │ (relational,│  │ Store   │  │ Lake      │ │
 │  │  blobs,    │  │  KV)       │  │  users)     │  │ (cache) │  │ (training │ │
 │  │  originals)│  │            │  │             │  │          │  │  data)    │ │
 │  └────────────┘  └────────────┘  └─────────────┘  └──────────┘  └──────────┘ │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### Layered mental model

1. **Edge/CDN** — Google's global edge network (GFE = Google Front End). Video segments cached at POPs near users.
2. **App/service layer** — upload, search, recommendation, comments, live, Shorts.
3. **ML/understanding layer** — search ranking, recommendation ranking, content understanding (CV/ASR), caption generation.
4. **Storage layer** — Colossus for video blobs, Bigtable for metadata KV, Spanner for relational (users, billing), MemoryStore (Redis-like) for cache.

---

## 3. Detailed Component Breakdown

### 3.1 Upload Service

- **What:** Handles creator uploads (resumable, chunked).
- **Why it exists:** Videos are large (up to 256GB for long videos). Resumable uploads survive flaky networks.
- **Flow:** Client requests upload session → uploads chunks → service assembles on Colossus → enqueues transcoding → returns video_id.

### 3.2 Transcoding Service (the cost center)

- **What:** Converts uploaded video into multiple codec/bitrate/resolution renditions.
- **Why it exists:** Adaptive streaming (HLS/DASH) requires multiple quality levels so the player can switch based on network speed. YouTube encodes each video into **many** renditions (codec × resolution × bitrate combinations).
- **Scale:** With 500 hours uploaded per minute, and each video getting ~10+ renditions, transcoding is one of the largest compute workloads at Google. They use custom encoding infrastructure.

```
   Original upload (4K, H.264)
            │
            ▼
   ┌────────────────────────┐
   │ Transcoding pipeline   │
   │ (custom + VP9/AV1)     │
   └────────────────────┘───┘
            │
   ┌────┬───┴────┬──────┬──────┬──────┬──────┐
   ▼    ▼        ▼      ▼      ▼      ▼      ▼
 144p 240p   360p   480p   720p  1080p  4K
 (each in H.264, VP9, AV1 where worthwhile)
            │
            ▼
   Segmented → CDN + Colossus
```

### 3.3 Search Service

- **What:** Returns ranked video results for a query.
- **Why it exists:** YouTube is the world's **second-largest search engine** (after Google). Search ranking is an ML problem.
- **Pipeline:** Query → tokenize → retrieve candidate videos (inverted index) → rank by ML model (relevance, quality, engagement, freshness) → return.

### 3.4 Recommendation Service (the engagement driver)

- **What:** Generates the home feed, "Up Next," and Shorts feed.
- **Why it exists:** ~70% of watch time comes from recommendations, not search. The rec model is one of Google's most valuable.
- **Pipeline:** Multi-stage — candidate generation (recall from many sources) → ranking DNN (predict watch time + satisfaction) → reranking (diversity, freshness).

### 3.5 Content Understanding (CV + ASR + OCR)

- **What:** ML models analyze every video to extract: objects/scenes in frames, spoken words (ASR → transcript), on-screen text (OCR), music IDs, safety/sponsor classification.
- **Why it exists:** Enables search over video *content* (not just titles), powers captioning, ad suitability, and cold-start recommendations.

### 3.6 Caption / Translation Service

- **What:** Generates auto-captions via ASR and translates them into 80+ languages.
- **Why it exists:** Accessibility + global reach. Auto-captions make videos discoverable across languages.

### 3.7 Live Streaming Service

- **What:** Ingests live video (RTMP/WebRTC), transcodes in real-time, distributes to viewers via CDN.
- **Why it exists:** Live has different constraints (low latency, real-time encoding) than VOD (video on demand).
- **Pattern:** Encode-once-at-source-quality, fanout-via-CDN; adaptive bitrate generated on the fly.

### 3.8 Shorts Feed

- **What:** Short-form vertical video feed (TikTok-style).
- **Why separate:** Different ranking (watch completion, loops), different UX (vertical, swipe), different creator economics.
- **Pipeline:** Same shape as TikTok's FYP — candidate generation → ranking → reranking.

### 3.9 Comment / Interaction Service

- **What:** Stores comments, likes, dislikes, shares, watch history.
- **Why it exists:** Engagement signals feed the recommendation model and are core product features.
- **Storage:** Bigtable / Spanner for durable, sharded by video_id.

### 3.10 Ad Service

- **What:** Inserts ads into videos (pre-roll, mid-roll, post-roll).
- **Why it exists:** YouTube's revenue engine. Ad targeting uses the same user features as recommendations.

---

## 4. Data Model

### 4.1 Video metadata (Bigtable / Spanner, simplified)

```sql
-- Core video metadata (Spanner — globally distributed, strongly consistent)
CREATE TABLE videos (
    id              STRING(MAX) PRIMARY KEY,  -- e.g., "dQw4w9WgXcQ"
    creator_id      INT64,
    title           STRING(MAX),
    description     STRING(MAX),
    duration_ms     INT64,
    category        STRING(64),
    privacy         STRING(16),  -- public, unlisted, private
    view_count      INT64,
    like_count      INT64,
    uploaded_at     TIMESTAMP,
    cdn_key         STRING(MAX)   -- maps to Colossus paths / CDN URLs
);

-- Users / channels
CREATE TABLE users (
    id              INT64 PRIMARY KEY,
    username        STRING(64),
    channel_id      INT64,
    subscriber_count INT64,
    created_at      TIMESTAMP
);
```

### 4.2 Video blobs (Colossus — Google's distributed FS)

```
   /videos/{video_id}/original.mp4                  ← source
   /videos/{video_id}/renditions/
       144p_h264.mpd  144p_h264_segments/
       240p_h264.mpd  240p_h264_segments/
       ...
       1080p_vp9.mpd  1080p_vp9_segments/
       4k_av1.mpd     4k_av1_segments/
```

### 4.3 Search index (inverted index, sharded)

```
   "cooking pasta" → [video_id_1, video_id_2, ...]  (with relevance scores)
   "guitar solo"   → [video_id_3, video_id_4, ...]
```

### 4.4 Interactions / watch history (Bigtable)

```
   row key:  user_id#video_id
   columns:  watch_time_ms, completion_pct, liked, subscribed_after, ts
```

### 4.5 Why this database mix?

| Data | DB | Why |
|---|---|--- exabyte-scale blobs, Google's GFS successor |  |
| Metadata KV | Bigtable | Billions of rows, low-latency reads |
| Relational (users, billing) | Spanner | Global ACID, strongly consistent |
| Cache | MemoryStore (Redis-like) | Sub-ms hot path |
| Search index | Custom (inverted index) | Search at catalog scale |
| Event lake | BigQuery / GCS | Massive scan for model training |

*(Render note: first row "Why" = exabyte-scale blobs, Google's GFS successor.)*

---

## 5. Request Flow — Uploading a Video and Watching It

### Upload flow (creator)

```
 Creator's browser             YouTube Backend
 ─────────────────             ───────────────
   1. Select video, fill title/desc
   2. POST /api/upload (resumable)
      │
      ├──── 3. Upload Service ─────▶ create session
      │                             store chunks → Colossus
      │                             assemble original
      │
      │                       4. Enqueue Transcoding
      │                          - generate 144p..4K renditions
      │                          - segment (HLS/DASH)
      │                          - push to CDN + Colossus
      │
      │                       5. Enqueue Content Understanding
      │                          - thumbnails generation
      │                          - ASR → captions
      │                          - CV → content tags
      │                          - safety / sponsor check
      │
      │                       6. Create videos row (Spanner)
      │                       7. Index for search
      │                       8. Notify subscribers (async)
      │
      │◀─── 9. "Upload processing..." ─
      │     (processing can take minutes for long videos)
```

### Watch flow (viewer searches and plays)

```
 Viewer's device               YouTube Backend
 ─────────────                 ───────────────
   1. Search "how to make pasta"
      │
      ├──── 2. Search Service ─────▶ retrieve candidates (inverted index)
      │                             rank by DNN (relevance, quality, engagement)
      │                             return top results
      │
      │◀─── 3. Search results ───────
      │
   4. Click video
      │
      ├──── 5. Video page ──────────▶ fetch metadata (Bigtable)
      │                             fetch comments
      │                             fetch "Up Next" from Rec Service
      │
      │◀─── 6. Video metadata ───────
      │     { video_url (CDN), captions, related }
      │
   7. Player requests video segments from CDN
      │
      ├──── 8. CDN edge ────────────▶ serves segments (cache hit if popular)
      │                             (cache miss → fetch from Colossus origin)
      │
   9. Adaptive bitrate: player switches quality based on network
  10. Viewer watches 8 minutes, likes, comments
      │
      │                       11. Interaction logged → event lake
      │                           → retrains rec models
```

### Why this design works

- **CDN absorbs the bandwidth.** Popular videos are cached at edges; origin (Colossus) only serves cache misses.
- **Transcoding is offline.** The viewer never waits for transcoding; it happens at upload time.
- **Search and rec are decoupled** from video delivery — each can scale independently.
- **Google infra is the moat.** Colossus, Bigtable, Spanner, edge network, and TPU/GPU compute are integrated and cheaper than any competitor can buy.

---

## 6. Scaling Strategy

### 6.1 Video delivery — CDN + adaptive streaming

YouTube's largest cost is bandwidth. The strategy:
- **Transcode once** into many renditions at upload.
- **Segment** into small chunks (2–10s).
- **Push to Google's global CDN.**
- **Player fetches from nearest edge**, switching bitrate adaptively.

Popular videos stay cached at edges; niche videos are fetched from origin on demand.

### 6.2 Colossus (GFS successor) for video blobs

Colossus is Google's distributed filesystem, designed for exabyte-scale. Videos are stored as chunks replicated across datacenters. It handles the scale that no off-the-shelf FS could.

### 6.3 Sharding by video_id and user_id

- **Metadata** sharded by `video_id` (Bigtable auto-shards by row key).
- **User data** sharded by `user_id` (Spanner handles globally).
- **Search index** sharded by term.

### 6.4 Search at catalog scale

YouTube's search index covers billions of videos. Techniques:
- **Inverted index** sharded by term.
- **Two-phase retrieval** — cheap retrieval (term match) → expensive ranking (DNN).
- **Content-based features** (from CV/ASR) let search match videos even when the title doesn't contain the query.

### 6.5 Recommendation at engagement scale

The recommendation model drives ~70% of watch time. Scaling:
- **Two-tower models** for candidate generation (user tower + video tower → dot product).
- **Ranking DNN** scores candidates on predicted watch time + satisfaction.
- **Feature store** precomputes user/video features.
- **Continuous retraining** from the event lake.

### 6.6 Multi-region

Google's infrastructure spans the globe. Data is replicated; users routed to nearest region. Live streaming has regional ingest points.

### 6.7 Encoding cost optimization

Transcoding 500 hours/min of video into 10+ renditions is astronomically expensive. YouTube optimizes with:
- **Custom encoding ASICs.**
- **Per-title encoding** (analyze each video's complexity; spend more bits on hard-to-compress content).
- **AV1 codec** for new content (better compression, more compute).
- **Lazy transcoding** for rare renditions (only generate 4K if someone requests it).

---

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|--- exabyte-scale blobs, replication |  |
| Metadata KV | Bigtable | Billions of rows, low-latency |
| Relational | Spanner | Global ACID, strong consistency |
| Cache | MemoryStore (Redis-like) | Sub-ms hot path |
| Search index | custom (inverted index) | Catalog-scale search |
| Event lake | BigQuery / GCS | Massive scan for training |
| ML | TensorFlow + TPU | Large rec/search models |
| Transcoding | FFmpeg + custom ASIC | Volume, cost |
| Edge | Google Front End (GFE) + CDN | TLS, caching, routing |
| Backend lang | C++, Go, Python, Java | Polyglot (perf + ML + infra) |
| Container | Borg → Kubernetes | Job orchestration |
| Streaming | HLS / DASH / WebRTC (live) | Adaptive streaming, realtime |

*(Render note: first row "Why" = exabyte-scale blobs, replication.)*

### Why Google infra is the moat

YouTube runs on Google's planet-scale infrastructure: Colossus (storage), Bigtable/Spanner (DBs), Borg/Kubernetes (orchestration), Andromeda (networking), TPU/GPU (ML). No competitor can replicate this stack — it's decades of compounding infrastructure investment. This is why YouTube's unit economics work at scale while rivals struggle.

---

## 8. How YOU Can Build a Simplified Version

You can build a **simple video-sharing app** in a few weekends. Here's how.

### 8.1 Tech choices

| Concern | Choice | Why |
|---|---|--- simple, ubiquitous |  |
| Backend | Node.js + Express OR Python + FastAPI | Fast iteration |
| DB | PostgreSQL | Metadata, users |
| Object storage | S3 | Videos |
| CDN | CloudFront or Bunny CDN | Delivery |
| Transcoding | FFmpeg (run via a job queue) | Multi-bitrate renditions |
| Search | Meilisearch | Simple video search |
| Recommender | "Up Next" = simple collaborative filter | Start simple |
| Frontend | React + Next.js | SSR, video player |
| Player | Video.js or hls.js | Adaptive streaming client |

*(Render note: first row "Why" = simple, ubiquitous.)*

### 8.2 Build order

1. **Auth + User model.** Signup, channels.
2. **Upload + Playback.** Upload to S3, `videos` table, basic player.
3. **Transcoding (basic).** FFmpeg to generate 2 renditions (360p, 720p). Package as HLS.
4. **Search.** Index title/description in Meilisearch.
5. **Home feed.** `SELECT * FROM videos ORDER BY views DESC LIMIT 50` (popular feed).
6. **Up Next.** "Videos by same creator" + "videos with same tags." Simple.
7. **Watch history + interactions.** Log views, likes.
8. **Simple recommender.** Collaborative filter: "users who watched X also watched Y." SQL or small model.
9. **Comments.** `comments` table.
10. **CDN.** Put video delivery behind a CDN.

### 8.3 Small-scale architecture

```
 ┌────────────┐     ┌──────────────────────┐     ┌────────────┐
 │ Browser /  │◀───▶│  Node.js + Express   │◀───▶│ PostgreSQL │
 │ Mobile     │ HTTPS│  - /upload          │ SQL │  videos     │
 └────────────┘     │  - /search          │     │  users      │
      │             │  - /feed            │     │  comments   │
      │             │  - /interactions    │     └────────────┘
      │             └──────────│───────────┘
      │                        │
      │             ┌──────────▼───────────┐
      │             │ Transcoder (FFmpeg)  │
      │             │ + Meilisearch       │
      │             └──────────│───────────┘
      ▼                        ▼
 ┌────────────────┐   ┌────────────────┐
 │ CDN (HLS)      │   │ S3 (originals)  │
 └────────────────┘   └────────────────┘
```

### 8.4 When you outgrow one box

- **Step 1:** CDN for video delivery (day one, honestly).
- **Step 2:** Dedicated transcoding workers (queue: RabbitMQ/SQS → workers).
- **Step 3:** Shard Postgres by `video_id`.
- **Step 4:** Move metadata to a KV store (DynamoDB / Cassandra).
- **Step 5:** Add content understanding (run pretrained CV model on uploads).
- **Step 6:** Build a real recommendation ranker (two-tower candidate gen + DNN ranker).
- **Step 7:** Stream interactions into a data lake; retrain models.
- **Step 8:** Multiple regions + global load balancing.

### 8.5 Estimated effort

- MVP (upload + playback + simple search): **1 weekend.**
- + Transcoding + HLS adaptive streaming: **+1 weekend.**
- + Comments + watch history + simple rec: **+1 weekend.**
- + Content understanding (CV model): **+2 weekends.**
- + DNN recommendation ranker: **+2–4 weekends (ML learning curve).**

---

## 9. Key Design Decisions & Trade-offs

### 9.1 Transcode-at-upload vs transcode-at-play

- **Choice:** Transcode all renditions at upload time.
- **Why:** Viewers never wait for transcoding; consistent quality. Origin only serves complete renditions.
- **Trade-off:** Massive upfront compute cost; long processing time for creators. YouTube mitigates with custom ASICs and lazy transcoding for rare renditions.

### 9.2 Multi-rendition adaptive streaming (HLS/DASH)

- **Choice:** Encode each video into many bitrate/resolution/codec variants.
- **Why:** Serves every network condition; minimizes buffering.
- **Trade-off:** Storage and compute multiply. Per-title encoding and AV1 mitigate.

### 9.3 Colossus / Bigtable / Spanner over off-the-shelf

- **Choice:** Google's internal storage stack.
- **Why:** Exabyte scale, global consistency (Spanner), low-latency KV (Bigtable). No off-the-shelf system matches this.
- **Trade-off:** Locked to Google infra. But YouTube is Google, so this is an advantage, not a constraint.

### 9.4 Recommendation drives 70% of watch time

- **Choice:** Heavy investment in rec models over search.
- **Why:** Recs increase watch time (and ad revenue) more than search.
- **Trade-off:** Discoverability via search is de-emphasized; creators must please the algorithm.

### 9.5 Content understanding for search and cold-start

- **Choice:** Run CV/ASR/OCR on every upload.
- **Why:** Enables content-based search ("show me videos with a golden retriever") and cold-start recs for new videos.
- **Trade-off:** Massive compute; tagging errors cause misclassification.

### 9.6 Separate Shorts / Live / VOD subsystems

- **Choice:** Different pipelines for Shorts, Live, and VOD.
- **Why:** Different constraints (latency, ranking, economics).
- **Trade-off:** Operational complexity.

### 9.7 Ads as first-class citizens

- **Choice:** Ad insertion is deeply integrated (pre/mid/post-roll, Shorts ads).
- **Why:** Revenue. Ad targeting reuses user features from recs.
- **Trade-off:** Viewer friction; ad blockers are a constant arms race.

---

## 10. Common Interview Questions

1. **How would you design YouTube?**
   Upload → transcoding → CDN; search → ranking; recommendation → ranking; storage → blobs + metadata KV + relational; scaling via CDN, sharding, Google infra.

2. **How does YouTube handle 500 hours of video uploaded per minute?**
   Resumable chunked upload, parallel transcoding farm (custom ASICs), multiple renditions, async content understanding. Processing is offline from the viewer's perspective.

3. **How is video delivered with low latency globally?**
   CDN with segmented adaptive streaming (HLS/DASH). Player fetches from nearest edge; switches bitrate on network changes. Popular videos cached at edges.

4. **How does the recommendation system work?**
   Multi-stage: candidate generation (two-tower, collaborative filtering, graph) → ranking DNN (predict watch time + satisfaction) → reranking (diversity, freshness). Features from a feature store; retrained from event lake.

5. **How does search ranking work?**
   Inverted index retrieves candidates; DNN ranks by relevance, quality, engagement, freshness. Content-based features (from CV/ASR) extend search beyond titles.

6. **How would you store the video blobs?**
   Distributed filesystem (Colossus/GFS). Videos as chunked, replicated files. CDN caches segments at edges.

7. **Why does YouTube transcode so many renditions?**
   Adaptive streaming needs multiple bitrate/resolution/codec variants to serve all networks and devices. Per-title encoding optimizes the set per video.

8. **How does live streaming differ from VOD?**
   Live uses real-time ingest (RTMP/WebRTC), on-the-fly transcoding, low-latency distribution (LL-HLS/WebRTC). VOD is transcoded offline and served from CDN.

9. **How does YouTube handle cold-start for new videos?**
   Content understanding (CV, ASR, OCR) extracts features from the video, letting the rec model score it before engagement history exists.

10. **Why does YouTube run on Google infra?**
    Colossus, Bigtable, Spanner, Borg/Kubernetes, edge network, TPU/GPU. The integration and scale are a competitive moat; unit economics only work at this scale.

---

## Appendix: Further Reading

- "YouTube at Exabyte Scale" (Google whitepapers, SIGCOMM talks).
- "Google's Colossus" / "Bigtable" / "Spanner" papers (OSDI/SOSP/TOCS).
- "Deep Neural Networks for YouTube Recommendations" (Covington et al., RecSys 2016) — THE canonical YouTube rec paper.
- Google's "Wide & Deep Learning."
- HLS (RFC 8216) and DASH (ISO/IEC 23009-1) adaptive streaming specs.
- FFmpeg documentation for transcoding.
- YouTube Engineering and Developers Blog.

---

*End of YouTube system design.*

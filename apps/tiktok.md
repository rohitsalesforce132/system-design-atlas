# TikTok — System Design Atlas

> **Audience:** A developer transitioning to AI/ML engineering who wants to understand how TikTok is built end-to-end. Plain English, real numbers, ASCII diagrams, basics-first.

---

## 1. Overview & Scale Numbers

TikTok is a **short-video app** whose defining feature is the **For You Page (FYP)** — an infinite, vertically-scrolled feed of videos personalized by a recommendation model that learns from every tap, pause, re-watch, and skip. The FYP is the product; everything else exists to feed it. TikTok is the canonical system for **recommendation-driven content discovery at population scale**.

### Scale (public numbers + estimates)

| Metric | Value | Why it matters |
|---|---|---|
| Monthly active users (MAU) | ~1.5–2 billion | Fastest-growing social app in history |
| Daily active users (DAU) | ~1 billion | Extremely sticky |
| Videos watched per day | ~1+ billion videos/day... per user ~1hr+/day | Insane watch time |
| Daily video uploads | ~30+ million | Constant fresh content needed for FYP |
| Average session length | ~95+ minutes/day per user | The model is *very* good |
| Videos served per second (peak) | millions of video streams/sec | Video delivery is the bandwidth cost |
| Recommendation model | multi-stage DNN (recall → rank → rerank) | The ML is the moat |
| Data centers | global (China + US + Singapore + EU) | Regulatory + latency |
| Engineers | thousands (ByteDance) | Parent company engineering |

### Why the numbers matter

TikTok is dominated by **two costs**: (1) **video bandwidth** — billions of short videos streamed daily, each requiring multiple bitrate encodings; and (2) **recommendation inference** — every video shown is the output of a DNN ranking over thousands of candidates, and the model is queried for *every scroll* for *every active user*. This dual load — massive video delivery + massive real-time ML inference — shapes the entire architecture.

### The one-paragraph summary

TikTok is a **recommendation system with a video player bolted on.** Uploads are ingested, transcoded into multiple bitrates, tagged by content-understanding models (vision + audio + text), and indexed. When you open the app, a multi-stage ranker (recall → ranking → reranking) picks the next video in milliseconds, serves it from a nearby CDN edge, and logs your interaction to retrain the model within hours. The FYP loop is the whole machine.

---

## 2. High-Level Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              USER DEVICES                                    │
 │                iOS · Android · Web · Smart TV                                │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │  HTTPS / QUIC / RTSP-like
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                          EDGE / CDN LAYER                                    │
 │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────────┐ │
 │  │ API Gateway  │  │ Video CDN    │  │  Edge ML inference (model cache)    │ │
 │  │ (auth, rate  │  │ (segmented   │  │  - reduces inference latency        │ │
 │  │  limit)      │  │  streaming)  │  │  - serves popular model versions    │ │
 │  └──────────────┘  └──────────────┘  └─────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                       APP / SERVICE LAYER                                    │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ Upload       │  │ Video        │  │ Feed / FYP   │  │ Interaction     │  │
 │  │ Service      │  │ Transcoding  │  │ Service      │  │ Logger          │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ User         │  │ Creator      │  │ Live         │  │ Notification    │  │
 │  │ Service      │  │ Analytics    │  │ Service      │  │ Service         │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                     RECOMMENDATION / ML LAYER                                │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
 │  │ Candidate    │  │ Ranking      │  │ Reranking    │  │ Feature Store   │  │
 │  │ Generation   │  │ Model (DNN)  │  │ (diversity,  │  │ (user + video   │  │
 │  │ (recall)     │  │              │  │  freshness)  │  │  features)      │  │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
 │  ┌──────────────┐  ┌──────────────┐                                          │
 │  │ Content      │  │ Training     │                                          │
 │  │ Understanding│  │ Pipeline     │                                          │
 │  │ (CV + ASR)   │  │ (retrain FYP)│                                          │
 │  └──────────────┘  └──────────────┘                                          │
 └──────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              STORAGE LAYER                                   │
 │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────┐  └──────────┐ │
 │  │ Video      │  │ Metadata   │  │ Graph / KV  │  │ Feature  │  │ Event     │ │
 │  │ Object     │  │ DB         │  │ Store       │  │ Store    │  │ Lake      │ │
 │  │ Storage    │  │ (MySQL)    │  │ (etcd/      │  │ (Redis + │  │ (HDFS /   │ │
 │  │            │  │            │  │  Redis)     │  │  KV)     │  │  S3-like) │ │
 │  └────────────┘  └────────────┘  └─────────────┘  └──────────┘  └──────────┘ │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### Layered mental model

1. **Edge/CDN** — videos are pre-transcoded into segments and cached at the edge. ML inference also runs at the edge for popular model versions to cut latency.
2. **App/service layer** — handles uploads, feed composition, user accounts, live, notifications.
3. **Recommendation/ML layer** — the brain. Candidate generation → ranking DNN → reranking. Plus content-understanding (CV/ASR) and the training pipeline.
4. **Storage layer** — video blobs, metadata DB, feature store, event lake for model training.

---

## 3. Detailed Component Breakdown

### 3.1 Upload Service

- **What:** Handles creator video uploads (chunked, resumable).
- **Why it exists:** Large files (up to ~500MB), need resumable uploads over flaky mobile networks.
- **Flow:** Client requests upload URL → uploads chunks → service assembles → enqueues transcoding job → returns video_id.

### 3.2 Video Transcoding Service

- **What:** Converts uploaded video into multiple bitrate/resolution variants (e.g., 240p, 360p, 480p, 720p, 1080p) and packs them for adaptive streaming (HLS/DASH).
- **Why it exists:** Different users have different network speeds. Adaptive streaming switches quality mid-playback to avoid buffering. One source video → many encoded renditions.
- **Scale:** This is a massive compute workload. ByteDance uses custom encoding ASICs/GPUs to cut cost.

```
   Original upload (1080p)
            │
            ▼
   ┌────────────────────┐
   │ Transcoding farm    │
   │ (GPU / ASIC)        │
   └────────────────────┘
            │
   ┌────┬───┴───┬───────┬───────┬───────┐
   ▼    ▼       ▼       ▼       ▼       ▼
 240p 360p   480p    720p   1080p   (ABR ladders)
   │    │       │       │       │
   └────┴───┬───┴───────┴───────┘
            ▼
   Segmented (HLS .ts / DASH chunks) → push to CDN
```

### 3.3 Feed / FYP Service (the heart)

- **What:** Composes the For You Page. For each "scroll," it calls the recommendation pipeline and returns the next batch of videos.
- **Why it exists:** The FYP is the product. It must return a ranked video in **< 100ms** to feel instant.
- **Flow:** Receive request → fetch user features → candidate generation (recall) → ranking DNN → reranking (diversity, freshness, ads) → return video IDs + CDN URLs.

### 3.4 Candidate Generation (Recall)

- **What:** Narrows the pool of ~billions of videos down to ~thousands of candidates that this specific user might like.
- **Why it exists:** You can't run a heavy DNN on billions of videos per request. You need a fast, cheap first pass.
- **Techniques:**
  - **Collaborative filtering** (users like you watched...).
  - **Content-based** (videos tagged similar to what you watched).
  - **Graph-based** (users ↔ videos graph embeddings).
  - **Trending/explore** (mix in globally popular content for serendipity).

### 3.5 Ranking Model (DNN)

- **What:** Scores each candidate (from recall) by **predicted watch time**, **completion rate**, and **engagement probability** (like, comment, share, follow).
- **Why it exists:** The ranker is the difference between a good feed and a great feed. TikTok's ranker is one of the most production-tuned DNNs in the world.
- **Architecture:** Multi-task DNN with wide-and-deep / deep learning towers. Inputs = user features + video features + context (time, device, network). Outputs = predicted watch time + engagement probs.

### 3.6 Reranking

- **What:** Adjusts the final order for **diversity** (don't show 5 cooking videos in a row), **freshness** (mix in new content), **ads** (insert sponsored videos), and **fairness** (spread exposure across creators).
- **Why it exists:** Pure predicted-watch-time ranking creates filter bubbles and creator monopolies. Reranking keeps the feed healthy.

### 3.7 Content Understanding (CV + ASR)

- **What:** ML models that "watch" and "listen to" every uploaded video to extract tags: objects in frames (dog, car, beach), spoken words (ASR → transcript), on-screen text (OCR), music track ID, aesthetic quality, safety classification.
- **Why it exists:** A brand-new video has no engagement history. Content understanding gives the ranker features to recommend it *before* anyone has watched it — this is TikTok's "cold start" advantage.

### 3.8 Interaction Logger + Event Lake

- **What:** Every interaction (watch, skip, pause, like, share, comment, follow) is logged to an event lake.
- **Why it exists:** This is the training data for the FYP model. TikTok retrains models **every few hours** with fresh interaction data.

### 3.9 Feature Store

- **What:** Stores precomputed user and video features (e.g., "user's last-7-day watch categories," "video's 24h completion rate") for the ranker.
- **Why it exists:** Computing features at request time is too slow. Precompute and cache; the ranker reads features in <10ms.

---

## 4. Data Model

### 4.1 Metadata DB (MySQL/PostgreSQL, sharded)

```sql
CREATE TABLE videos (
    id              BIGINT PRIMARY KEY,
    creator_id      BIGINT,
    caption         TEXT,
    cdn_url_base    TEXT,           -- e.g., https://cdn.tiktok.com/v/{id}/
    duration_ms     INT,
    sound_id        BIGINT,         -- audio track reference
    hashtags        TEXT[],         -- array of hashtags
    created_at      TIMESTAMP,
    view_count      BIGINT,
    like_count      BIGINT,
    share_count     BIGINT,
    safety_label    VARCHAR(32)     -- safe, review, remove
);

CREATE TABLE users (
    id              BIGINT PRIMARY KEY,
    username        VARCHAR(64),
    follower_count  INT,
    following_count INT,
    created_at      TIMESTAMP
);

CREATE TABLE interactions (
    id              BIGINT,
    user_id         BIGINT,
    video_id        BIGINT,
    watch_time_ms   INT,
    completion_pct  REAL,
    liked           BOOLEAN,
    shared          BOOLEAN,
    commented       BOOLEAN,
    timestamp       BIGINT
);
```

### 4.2 Feature store (Redis + KV)

```
   key:   user_features:{user_id}
   value: {
     "last_7d_categories": {"cooking": 0.4, "comedy": 0.3, ...},
     "avg_watch_time_ms":  18000,
     "preferred_duration": 30000,
     ...
   }

   key:   video_features:{video_id}
   value: {
     "tags":       ["dog", "beach", "sunset"],
     "duration":   15000,
     "24h_ctr":    0.12,
     "creator_id": 12345,
     ...
   }
```

### 4.3 Event lake (HDFS / S3-like, parquet)

```
   /events/interactions/yyyy/mm/dd/
       part-00001.parquet   (user_id, video_id, watch_time_ms, liked, ...)
       part-00002.parquet
       ...
```

### 4.4 Why this database mix?

| Data | DB | Why |
|---|---|--- sharded relational for core entities |  |
| User/video features | Redis + KV feature store | Sub-10ms reads for the ranker |
| Video blobs | Object storage + CDN | Large blobs, global delivery |
| Interactions (events) | Event lake (Parquet on HDFS/S3) | Massive scan for model training |
| Graph (users↔videos) | Graph DB or embeddings | Recall |

*(Render note: first row "Why" = sharded relational for core entities.)*

### 4.5 The cold-start trick

A brand-new video has no view/like history. How does the ranker score it? **Content understanding features** (tags from CV/ASR) give the ranker signal even on day zero. Then, as early watchers react, the video's engagement features update and it either gets amplified or fades. This is TikTok's core advantage over social-graph feeds.

---

## 5. Request Flow — Opening the App and Watching the First Video

> **Alice opens TikTok. The first video must appear in < 1 second.**

```
 Alice's phone                     TikTok Backend
 ─────────────                     ──────────────
      │
   1. Opens app
   2. GET /api/feed/fyp
      { user_id, device, network, location }
      │
      ├──── 3. API gateway ────────▶ [Auth, rate limit]
      │
      │                       4. FYP Service
      │                          a. Fetch user features (Redis)         ~5ms
      │                          b. Candidate Generation (recall)      ~20ms
      │                             → ~2,000 candidate video_ids
      │                          c. Fetch video features (Redis)       ~10ms
      │                          d. Ranking DNN scores each            ~30ms
      │                             → top ~50 by predicted watch time
      │                          e. Reranking (diversity, ads)         ~10ms
      │                             → final ordered list of ~6 videos
      │                          f. Return video_ids + CDN URLs
      │
      │◀─── 5. JSON response ───────
      │     { videos: [ {video_id, cdn_url, caption} ] }
      │
   6. Pre-fetch first 2 videos from CDN
   7. Autoplay video 1
      │
      │                       8. Alice watches for 12s, skips at 13s
      │                          - Interaction Logger records:
      │                            {user: Alice, video: v1, watch: 13s, skip: true}
      │
   9. As Alice scrolls, FYP fetches next batch using updated features
```

### Why this design works

- **Multi-stage ranking** keeps the per-request compute bounded: recall is cheap (narrows billions → thousands), ranking is expensive (thousands → 50), rerank is cheap (50 → 6).
- **Feature store** precomputes features so the ranker doesn't do DB lookups at request time.
- **Edge ML inference** reduces ranking latency for users far from a datacenter.
- **Content understanding** enables cold-start for new videos.
- **Fast retraining loop** (hours) keeps the model fresh on trends.

### The upload flow (creator side)

```
 Creator's phone                TikTok Backend
 ─────────────                  ──────────────
   1. Record / select video
   2. POST /api/upload (chunked)
      │
      ├──── 3. Upload Service ─────▶ assemble → store original
      │
      │                       4. Enqueue transcoding job
      │                          - transcode to 240p/360p/.../1080p
      │                          - segment into HLS chunks
      │                          - push to CDN
      │
      │                       5. Enqueue content understanding
      │                          - CV: extract frame tags
      │                          - ASR: transcript
      │                          - OCR: on-screen text
      │                          - Music ID
      │                          - Safety check
      │
      │                       6. Create videos row (metadata)
      │                       7. Video is now candidate-eligible
      │
      │◀─── 8. "Video published!" ─
```

---

## 6. Scaling Strategy

### 6.1 Video delivery — CDN is everything

TikTok's largest cost is video bandwidth. Every video is:
1. Transcoded into multiple bitrates.
2. Segmented into small chunks (HLS/DASH).
3. Pushed to CDN edges globally.

When you watch, the player fetches segments from the nearest edge. Adaptive bitrate switching keeps playback smooth on fluctuating networks.

### 6.2 ML inference scaling

The ranker runs **billions of times per day**. Scaling strategies:
- **Model quantization** (float32 → int8) to cut inference cost.
- **Edge inference** for popular model versions.
- **Batch inference** where latency allows.
- **Feature caching** to avoid recomputing.

### 6.3 Feature store at scale

User and video features are precomputed by stream processors (Flink/Spark Streaming) reading the interaction event stream. Features update in near-real-time (seconds to minutes).

### 6.4 Event lake + frequent retraining

Interaction events flow into a data lake. Training pipelines read the lake, retrain the ranker, and deploy new model versions **every few hours**. This is why TikTok feels responsive to your interests within a single session.

### 6.5 Sharding

- **Metadata DB** sharded by `video_id` or `creator_id`.
- **Feature store** sharded by `user_id` / `video_id`.
- **Event lake** partitioned by date.

### 6.6 Multi-region deployment

ByteDance operates separate stacks per region (China = Douyin, US/EU/Asia = TikTok) for regulatory compliance (data sovereignty) and latency.

---

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|--- massive video delivery, adaptive bitrate, global edges |  |
| API gateway | custom + Kubernetes | L7 routing, auth |
| App services | Go, C++, Python | Go for I/O, C++ for perf, Python for ML |
| Transcoding | custom + FFmpeg + GPU/ASIC | Volume, cost |
| Metadata DB | MySQL (sharded) / TiDB (NewSQL) | Relational, scale |
| Feature store | Redis + custom KV | Sub-10ms reads |
| Event lake | HDFS / S3-like + Parquet | Massive scan for training |
| Stream processing | Apache Flink / Spark Streaming | Near-real-time features |
| ML training | PyTorch + custom distributed | Large models |
| ML serving | TensorFlow / custom + TensorRT | Low-latency inference |
| Graph / embeddings | custom + Faiss / Milvus | Vector similarity for recall |
| Mobile | Native (Swift/Kotlin) | Performance, camera access |
| Load balancing | custom L4/L7 | Global traffic |

*(Render note: first row "Why" = massive video delivery, adaptive bitrate, global edges.)*

### Why Go + C++ + Python?

- **Go** for high-concurrency I/O services (APIs, loggers).
- **C++** for performance-critical paths (transcoding, media processing).
- **Python** for ML (training, content understanding).
This polyglot approach lets each layer use the best tool.

---

## 8. How YOU Can Build a Simplified Version

You can't build TikTok's ML overnight, but you can build a **simple short-video app with a basic recommender** in a few weekends.

### 8.1 Tech choices

| Concern | Choice | Why |
|---|---|--- simple, ubiquitous |  |
| Backend | Python + FastAPI | Fast iteration, ML-friendly |
| DB | PostgreSQL | Metadata, users |
| Object storage | S3 | Videos |
| CDN | CloudFront or Bunny CDN | Video delivery |
| Recommender | Simple content-based + collaborative filtering in Python | Learnable in a weekend |
| Frontend | React Native or Next.js | Cross-platform |
| ML (later) | scikit-learn → LightGBM → PyTorch | Start simple, grow |

*(Render note: first row "Why" = simple, ubiquitous.)*

### 8.2 Build order

1. **Auth + User model.** Signup, JWT.
2. **Upload + Playback.** Upload video to S3, `videos` table, basic player in frontend.
3. **Transcoding (basic).** Use FFmpeg to generate at least 2 resolutions. (Skip adaptive streaming at first; just pick a default.)
4. **Hashtags + captions.** Add hashtags to videos for simple content-based recall.
5. **Simple recommender v1 (content-based).** `videos WHERE hashtags IN (user's liked hashtags) ORDER BY recency`. Chronological-with-light-filter.
6. **Interaction logging.** Log every watch/skip/like to a `interactions` table.
7. **Recommender v2 (collaborative).** "Users who liked what you liked also liked..." Simple SQL or a small LightGBM model on features [hashtag, duration, creator, hour_of_day] → predict watch_time.
8. **Recommender v3 (DNN, optional).** Embedding model (user embedding + video embedding dot product). Train on interactions. This is the "real" TikTok-style ranker, simplified.
9. **CDN.** Move video delivery behind a CDN.
10. **Infinite scroll UI.** Frontend prefetches next batch.

### 8.3 Small-scale architecture

```
 ┌────────────┐     ┌──────────────────────┐     ┌────────────┐
 │ Mobile /   │◀───▶│  FastAPI (Python)    │◀───▶│ PostgreSQL │
 │ Web client │ HTTPS│  - /feed (ranker)    │ SQL │  videos     │
 └────────────┘     │  - /upload           │     │  users      │
      │             │  - /interactions     │     │  interactions│
      │             └──────────│───────────┘     └────────────┘
      │                        │
      │             ┌──────────▼───────────┐
      │             │ Recommender (Python) │
      │             │ - content-based      │
      │             │ - LightGBM ranker    │
      │             └──────────│───────────┘
      │                        │
      ▼                        ▼
 ┌────────────────┐   ┌────────────────┐
 │ CDN (videos)   │   │ S3 (originals)  │
 └────────────────┘   └────────────────┘
```

### 8.4 When you outgrow one box

- **Step 1:** Move video delivery to CDN (day one, honestly).
- **Step 2:** Shard Postgres by `video_id`.
- **Step 3:** Add a feature store (Redis) for precomputed user/video features.
- **Step 4:** Stream interactions into a data lake (S3 + Parquet).
- **Step 5:** Add a real candidate-generation stage (vector embeddings + ANN index like Faiss).
- **Step 6:** Move ranker to a dedicated inference server (TF Serving / Triton).
- **Step 7:** Add content understanding (run a pretrained CV model on uploads).
- **Step 8:** Retrain models on a schedule (hourly/daily).

### 8.5 Estimated effort

- MVP (upload + playback + chronological feed): **1 weekend.**
- + Simple content-based recommender: **+1 weekend.**
- + Interaction logging + collaborative filtering: **+1 weekend.**
- + DNN embedding ranker: **+2–4 weekends (ML learning curve).**
- + Content understanding (CV/ASR): **+1–2 weekends.**

---

## 9. Key Design Decisions & Trade-offs

### 9.1 Recommendation-first, not social-graph-first

- **Choice:** The FYP shows videos from *creators you don't follow*, ranked by predicted engagement.
- **Why:** Maximizes discovery and watch time. New creators can break out without an existing following.
- **Trade-off:** Less "social" connection; creators have less reliable reach (algorithm-dependent).

### 9.2 Multi-stage ranking (recall → rank → rerank)

- **Choice:** Funnel billions of videos down to ~6 shown.
- **Why:** You can't run a heavy DNN on billions of candidates per request. Stages keep compute bounded.
- **Trade-off:** Each stage can discard videos the next stage would have loved. Tuning stage boundaries is an art.

### 9.3 Content understanding for cold start

- **Choice:** Run CV + ASR + OCR on every upload.
- **Why:** New videos have no engagement history. Content features let the ranker recommend them immediately.
- **Trade-off:** Massive compute cost; tagging errors can misroute videos.

### 9.4 Hours-scale retraining

- **Choice:** Retrain the ranker every few hours.
- **Why:** Trends move fast; a stale model misses them.
- **Trade-off:** Operational complexity; risk of model regression; need canary deployment.

### 9.5 Edge ML inference

- **Choice:** Run popular model versions at CDN edges.
- **Why:** Cuts ranking latency for users far from core datacenters.
- **Trade-off:** More infrastructure; model version drift between edge and core.

### 9.6 Video transcoding ladder (ABR)

- **Choice:** Encode each video into 4–6 bitrate renditions.
- **Why:** Adaptive streaming serves slow and fast networks from the same source.
- **Trade-off:** Storage and compute cost multiply. ByteDance uses custom silicon to manage this.

### 9.7 Separate stacks per region (Douyin vs TikTok)

- **Choice:** Distinct backends per geography.
- **Why:** Data sovereignty laws (China, EU), content moderation differences, latency.
- **Trade-off:** Duplicated engineering effort; cross-region feature parity is hard.

---

## 10. Common Interview Questions

1. **How does the TikTok For You Page work?**
   Multi-stage: candidate generation (recall) → ranking DNN (predicted watch time + engagement) → reranking (diversity, ads). Features from a feature store; content understanding for cold start.

2. **How do you serve personalized video recommendations at scale?**
   Precompute user/video features in a feature store; cheap recall narrows candidates; GPU/quantized DNN ranks; edge inference cuts latency; CDN delivers video.

3. **How does TikTok handle cold-start for new videos?**
   Content understanding (CV, ASR, OCR) extracts tags from the video itself. These features let the ranker score the video before any engagement data exists.

4. **How is video transcoding handled?**
   Original uploaded → transcoding farm (FFmpeg + GPU/ASIC) → multiple bitrate renditions (ABR ladder) → segmented (HLS/DASH) → pushed to CDN.

5. **What's the difference between TikTok's feed and Instagram's?**
   TikTok is interest-graph (algorithmic, creator-agnostic); Instagram is social-graph (follow-based). TikTok's cold-start via content understanding is the key differentiator.

6. **How does the ranking model train?**
   Interaction events (watch time, skips, likes) flow to an event lake. Training pipelines read the lake, compute features, retrain the DNN, and deploy every few hours.

7. **How do you avoid filter bubbles?**
   Reranking enforces diversity (don't show 5 similar videos), freshness (mix in new content), and creator fairness (spread exposure).

8. **Why a feature store instead of computing features at request time?**
   Request-time feature computation is too slow (DB lookups, aggregations). Precompute and cache; ranker reads in <10ms.

9. **How does TikTok deliver video with low latency globally?**
   CDN with segmented adaptive streaming. Player fetches from nearest edge; switches bitrate based on network.

10. **How would you build a simplified TikTok?**
    Upload to S3, FFmpeg for transcoding, Postgres for metadata, simple content-based recommender (hashtags), log interactions, upgrade to collaborative filtering, then to a DNN embedding model. CDN for delivery.

---

## Appendix: Further Reading

- ByteDance tech blog (machine learning, recommendation systems).
- "Deep Learning Recommendation Model for Personalization and Recommendation Systems" (DLRM).
- "Wide & Deep Learning for Recommender Systems" (Google, conceptually similar).
- Faiss / Milvus docs for vector similarity search.
- HLS and DASH adaptive streaming specs.
- TikTok Transparency reports (content moderation, safety labeling).

---

*End of TikTok system design.*

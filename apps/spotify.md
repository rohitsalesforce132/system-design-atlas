# Spotify — System Design

> **Reader's note:** This is a deep, standalone walkthrough. Read top to bottom and you will understand how Spotify works end-to-end — the streaming pipeline, the recommendation engine, the data model, the trade-offs, and how to build a simplified clone yourself. No buzzwords without explanation.

---

## 1. Overview & Scale Numbers

Spotify is an on-demand audio streaming service. A user searches for a song, hits play, and within ~1 second music starts flowing from a server near them. Behind that single tap sits one of the largest streaming + machine-learning infrastructures in the world.

### Why is this hard?

Three hard problems combine:

1. **Streaming at scale.** Audio is large and latency-sensitive. A one-second stall is a user complaint. You must serve millions of simultaneous listeners from locations near them.
2. **The catalog.** 100M+ tracks, each requiring metadata, audio files at multiple bitrates, and indexes for search.
3. **Personalization.** "Discover Weekly", "Daily Mixes", and the home feed are unique to each of 600M+ users, recomputed regularly from billions of listening events.

### Real-world scale (publicly reported / industry estimates)

| Metric | Approximate value |
|---|---|
| Monthly active users (MAU) | ~675M |
| Premium (paying) subscribers | ~270M |
| Catalog (tracks) | 100M+ |
| Catalog (podcasts) | ~6M |
| Tracks streamed per day | billions |
| Concurrent listeners (peak) | ~30M+ |
| Songs in user playlists | tens of billions |
| Audio stored | ~10+ PB |
| Recommendation models | hundreds (tens of millions of playlists used as training data) |
| Average song file size | ~3–10 MB (depends on bitrate / codec) |
| Geographic presence | 180+ countries, 65+ markets with local data centers |

**Storage math (back-of-envelope):**

- 100M tracks × avg 3 files per track (different bitrates) × ~5 MB = ~1.5 PB of audio alone.
- Add podcasts, music videos, and high-fidelity formats → multiple PB.
- User data, play history, and ML feature stores add more.

**Latency / quality targets:**

- **Time-to-first-audio (TTFA):** < 1 second from tap to sound.
- **Rebuffering ratio:** < 1% of total play time.
- **Bitrates:** 96 kbps (low) up to 320 kbps (premium high quality), plus lossless/FLAC for premium tiers in supported markets.
- **Codec:** historically Ogg Vorbis (mobile), AAC (web), now also Opus and FLAC.

---

## 2. High-Level Architecture

Spotify's architecture is famous for its early **hybrid peer-to-peer (P2P)** design. Today the P2P layer is largely gone (mobile-first world), but the core idea — **keep audio close to the user** — remains.

```
                        ┌──────────────────────────────────────────────┐
                        │                  USERS                        │
                        │  mobile, desktop, web, smart speakers, cars   │
                        └──────────────────────┬───────────────────────┘
                                               │  HTTPS (metadata, API)
                                               │  + audio stream (HTTPS/RTP)
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │            EDGE / CDN                         │
                        │   - audio chunks cached at edge              │
                        │   - TLS termination, DDoS protection          │
                        └──────────┬───────────────────┬───────────────┘
                                   │                   │
                     metadata/API  │                   │ audio bytes
                                   ▼                   ▼
                        ┌──────────────────┐  ┌────────────────────────┐
                        │  LOAD BALANCER   │  │  STREAMING CDN /       │
                        │  (envoy / LB)    │  │  AUDIO EDGE NODES      │
                        └────────┬─────────┘  └──────────┬─────────────┘
                                 │                       │
        ┌────────────────────────┼────────────────────────┼────────────────┐
        ▼                        ▼                        ▼                 ▼
┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐  ┌──────────────┐
│  API Gateway   │    │  Metadata /      │   │  Streaming       │  │  Recommend-  │
│  (auth, rate   │    │  Catalog Svc     │   │  Backend         │  │  ation Svc   │
│   limit)       │    │  (tracks, albums,│   │  (audio file     │  │  (Discover,  │
└───────┬────────┘    │   artists)       │   │   delivery)      │  │   Radio, Home)│
        │             └─────────┬────────┘   └────────┬─────────┘  └──────┬───────┘
        │                       │                     │                   │
        ▼                       ▼                     ▼                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              MICROSERVICES                                   │
│  ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌─────────────┐ │
│  │ Playlist │ │ Play Queue │ │ Social     │ │ Search      │ │ Billing /  │ │
│  │ Service  │ │ Service    │ │ (Follow,   │ │ Service     │ │ Subscriber │ │
│  │          │ │            │ │  Collab)   │ │             │ │ Service    │ │
│  └────┬─────┘ └─────┬──────┘ └─────┬──────┘ └──────┬──────┘ └─────────────┘ │
└───────┼─────────────┼──────────────┼───────────────┼────────────────────────┘
        │             │              │               │
        ▼             ▼              ▼               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                       │
│  ┌────────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Postgres   │ │ Cassandra    │ │ Redis    │ │ Kafka    │ │ Object Store │ │
│  │ (users,    │ │ (play history│ │ (cache,  │ │ (events: │ │ (S3 / GCS:   │ │
│  │  billing,  │ │  playlists,  │ │  sessions│ │  plays,  │ │  audio files │ │
│  │  catalog)  │ │  social)     │ │  queues) │ │  clicks) │ │  metadata)   │ │
│  └────────────┘ └──────────────┘ └──────────┘ └──────────┘ └──────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘

                                        │
                                        ▼
                        ┌──────────────────────────────────────────────┐
                        │     BIG DATA / ML PIPELINE (offline)          │
                        │   Kafka → S3 → Spark/Batch → Feature Store    │
                        │   → train recommendation models → deploy      │
                        │   (Discover Weekly batch job, nightly)        │
                        └──────────────────────────────────────────────┘
```

### Walking the diagram

1. **Edge/CDN** serves audio chunks (cached close to users) and terminates TLS.
2. **API path** (left) handles metadata, search, playlists, recommendations — pure request/response.
3. **Audio path** (right) is a separate, high-throughput streaming path. Splitting metadata from audio is deliberate: they have very different latency and throughput profiles.
4. **Microservices** each own their storage (polyglot persistence).
5. **Kafka** captures every play event, click, and skip. This firehose powers personalization, royalty payouts, and analytics.
6. **Offline ML pipeline** consumes Kafka, trains models, and produces recommendation outputs (e.g., your Discover Weekly, refreshed weekly).

---

## 3. Detailed Component Breakdown

### 3.1 Client (Mobile / Desktop / Web)

The client is surprisingly smart. Historically the Spotify desktop client did significant work:

- **P2P peer:** served audio chunks to other nearby listeners (now mostly retired on mobile).
- **Local cache:** keeps recently played audio files on-device to avoid re-downloading.
- **Prefetch:** while you listen to song N, the client prefetches song N+1 (and N+2) so playback crosses track boundaries seamlessly.
- **Adaptive bitrate:** monitors network conditions and requests higher/lower quality chunks dynamically.

### 3.2 Streaming Backend / Audio CDN

Audio files are stored as **chunked files** in object storage (S3/GCS-compatible). Edge nodes around the world cache hot chunks. When you press play:

1. Client requests the first chunk of the track from the nearest edge.
2. If cached, edge serves immediately. If not, edge fetches from origin object store and caches.
3. Client requests subsequent chunks; prefetch logic runs on the client.

Spotify historically ran their own edge servers in data centers worldwide (co-located or rented). Today they also use commercial CDNs (e.g., Google Cloud CDN / Akamai) in a multi-CDN strategy.

**Adaptive bitrate:** multiple encodings of each track exist (96/160/320 kbps, plus lossless). The client picks a bitrate based on network quality, available storage, and user settings ("Audio Quality" in the app).

### 3.3 Metadata / Catalog Service

Owns the **canonical** information about tracks, albums, artists, podcasts:

- Title, artist, album, duration, ISRC, release date, explicit flag, genre tags.
- Cover art URLs, preview clips.
- **Rights and licensing info** (which regions can play this track, what royalty rate applies).

The catalog is relatively static (100M tracks, growing slowly) and heavily cached. Backed by a relational database (Postgres historically) with a read-through cache in front.

### 3.4 Playlist Service

- Owns user-created and algorithmic playlists.
- **CRUD operations:** create, add tracks, reorder, delete.
- **Collaborative playlists:** real-time updates via WebSocket when multiple users edit.
- Backed by **Cassandra** (or a similar horizontally scalable store) because:
  - Billions of playlists, each a list of track IDs.
  - Write patterns are append-mostly.
  - Availability is favored over strong consistency.

### 3.5 Play Queue Service

Manages the "up next" queue: the ordered list of tracks the user will hear next. Tracks state transitions: playing, paused, skipped, repeated. State lives in Redis for low-latency access.

### 3.6 Search Service

- Inverted index (Elasticsearch / Lucene) over the entire catalog.
- Indexes: track title, artist, album, lyrics (where licensed), podcast names.
- Supports fuzzy matching ("did you mean..."), typo tolerance, and ranking (popularity + relevance).
- Search infrastructure must handle millions of queries/sec at peak, so it's heavily sharded and replicated.

### 3.7 Recommendation Service

This is where the magic happens. Spotify's recommendations are powered by multiple complementary approaches:

**Collaborative Filtering (the classic approach):**

- Treat playlists as implicit feedback: "users who put track A and track B in the same playlist probably find them similar."
- Build a giant co-occurrence matrix of tracks.
- For a user, recommend tracks that co-occur with their favorites but they haven't heard.

**Audio Analysis (content-based):**

- Spotify runs every uploaded track through an audio-analysis pipeline that extracts features: tempo, key, loudness, timbre, danceability, energy, acousticness, valence.
- These features (the result of ML models on the raw audio) let the system find "songs that sound similar" even for brand new tracks with zero play history. This is how Spotify solves the **cold-start problem** better than pure collaborative filtering.
- Open source proof: Spotify open-sourced these audio features via their Web API for years (the "Audio Features" endpoint).

**Deep Learning / Sequence Models:**

- Modern Spotify uses sequence models (RNNs, transformers) over listening history to predict the next play.
- Embeddings: tracks and users are embedded into a shared vector space. Recommendations = nearest neighbors in that space.

**Notable products and how they're powered:**

- **Discover Weekly:** a batch job that runs nightly/weekly, generating a fresh 30-track playlist per user from collaborative filtering + audio analysis + diversity constraints.
- **Daily Mix:** clustered groupings of the user's listening habits (e.g., "your rock tracks", "your hip-hop tracks").
- **Release Radar:** new releases from artists you follow or who are similar.
- **Home feed:** a real-time ranked feed of carousels (made-for-you, made-for-everyone, popular, etc.).

### 3.8 Social Service

- Follow artists, follow friends.
- See what friends are listening to (the "Friend Activity" sidebar on desktop).
- Collaborative playlists.

### 3.9 Event Pipeline (Kafka)

Every meaningful action is an event: `track_played`, `track_skipped`, `track_liked`, `playlist_created`, `search_performed`, `session_start`. Kafka captures these at massive throughput. Consumers:

- **Royalty payout system:** counts plays per track for rights-holder payments.
- **ML feature store:** updates user features used by recommendation models.
- **Analytics dashboards:** for Spotify employees and (aggregated) for artists ("Spotify for Artists").

### 3.10 Big Data / ML Pipeline (offline)

```
   User events ──▶ Kafka ──▶ S3 data lake ──▶ Spark batch jobs
                                                   │
                                                   ▼
                                          Feature Store
                                          (user features,
                                           track features,
                                           embeddings)
                                                   │
                                                   ▼
                                          Train models (CF, DL)
                                                   │
                                                   ▼
                                          Deploy to Recommendation Service
```

Discover Weekly, for example, is a **batch pipeline**: it runs nightly, reads the user's last 6 months of listening, generates recommendations, writes the playlist back to the Playlist Service. The playlist you see Monday morning was computed Sunday night.

---

## 4. Data Model

### 4.1 Catalog (Postgres / relational)

```
artists
────────────────────────────────────────
artist_id       UUID PK
name            TEXT
popularity      INT          -- 0..100, updated by batch job
genres         TEXT[]         -- array of genre tags
monthly_listeners INT         -- denormalized
image_url       TEXT

albums
────────────────────────────────────────
album_id        UUID PK
artist_id       UUID FK
title           TEXT
release_date    DATE
album_type      TEXT          -- 'album','single','compilation'
total_tracks    INT

tracks
────────────────────────────────────────
track_id        UUID PK
album_id        UUID FK
title           TEXT
duration_ms     INT
explicit        BOOLEAN
popularity      INT           -- 0..100
isrc            TEXT UNIQUE   -- industry standard id
preview_url     TEXT          -- 30-sec preview clip
audio_features  JSONB         -- danceability, energy, key, tempo, ...
file_96_url     TEXT          -- bitrate variants
file_160_url    TEXT
file_320_url    TEXT
file_lossless_url TEXT
```

### 4.2 Users (Postgres)

```
users
────────────────────────────────────────
user_id         UUID PK
email           TEXT UNIQUE
username        TEXT
country_code    CHAR(2)       -- drives catalog availability & price
birthdate       DATE
tier            TEXT          -- 'free','premium'
created_at      TIMESTAMPTZ
```

### 4.3 Play history (Cassandra)

Cassandra is ideal for time-series play history — write-heavy, append-only, queried by `(user_id, time)`.

```sql
CREATE TABLE play_history (
    user_id    UUID,
    day_bucket DATE,            -- partition key component (TTL ~1 yr)
    played_at  TIMESTAMP,
    track_id   UUID,
    context    TEXT,            -- 'playlist','album','radio','search'
    ms_played  INT,             -- how much of the track was actually heard
    PRIMARY KEY ((user_id, day_bucket), played_at)
) WITH CLUSTERING ORDER BY (played_at DESC);
```

- **Partition key** `(user_id, day_bucket)` ensures all of a user's plays on a given day live on one node — fast range scans.
- **TTL** auto-expires old data (e.g., after 1 year) to bound storage.

### 4.4 Playlists (Cassandra)

```sql
CREATE TABLE playlists (
    playlist_id   UUID,
    user_id       UUID,           -- owner
    name          TEXT,
    is_collaborative BOOLEAN,
    track_ids     LIST<UUID>,     -- ordered list of tracks
    added_at      LIST<TIMESTAMP>,
    PRIMARY KEY (playlist_id)
);

CREATE TABLE playlists_by_user (
    user_id       UUID,
    playlist_id   UUID,
    name          TEXT,
    PRIMARY KEY (user_id, playlist_id)
);
```

Why Cassandra and not Postgres for playlists?

- Billions of rows, mostly append-mostly.
- Availability > consistency: a playlist briefly missing a track during a network partition is acceptable.
- Linear scalability by adding nodes.

### 4.5 Cache (Redis)

- **Sessions** (logged-in user state).
- **Hot track metadata** — top 10k tracks served from Redis.
- **Play queues** for active sessions.
- **Recommendation results** — precomputed lists per user, refreshed nightly.

### 4.6 Audio storage (object store)

Audio files are opaque blobs. Each track has multiple encodings (bitrates). The catalog DB stores URLs/keys pointing into the object store. The audio bytes themselves never touch a relational DB.

### 4.7 Why these databases?

| Need | Choice | Reason |
|---|---|---|
| Catalog (small, relational, mostly static) | Postgres | ACID, joins, mature. |
| Users & billing | Postgres | Transactional integrity (money). |
| Play history (massive time-series) | Cassandra | Write-heavy, time-bucketed, scalable. |
| Playlists | Cassandra | Billions of rows, append-mostly. |
| Cache / sessions / queues | Redis | Sub-ms latency. |
| Search index | Elasticsearch | Full-text, fuzzy, ranked. |
| Audio files | Object store (S3/GCS) | Cheap blob storage; edge-cached. |
| Events | Kafka | Durable, partitioned, replayable. |
| Analytics / ML | S3 + Spark / Scio | Batch processing at PB scale. |

**Polyglot persistence** — each workload gets the right tool.

---

## 5. Request Flow — Playing a Song

This is the core user action. Let's trace it end to end.

### 5.1 The play path

```
 User taps a track in a playlist
              │
              ▼
   ┌──────────────────────┐
   │  Client              │  1. Determine audio file URL for chosen track
   │  (mobile/desktop)    │     (and bitrate based on network + settings)
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Edge / CDN          │  2. Request first audio chunk from nearest edge
   └──────────┬───────────┘
              │
              │  cache hit? ──────yes─────────┐
              │                                │
              │ no                             │
              ▼                                │
   ┌──────────────────────┐                    │
   │  Origin Object Store │  3. Fetch full     │
   │  (S3 / GCS)          │     audio file     │
   └──────────┬───────────┘                    │
              │                                │
              ▼                                ▼
   ┌──────────────────────┐
   │  Edge caches chunk   │  4. Edge caches chunk for future requests
   │  + streams to client │     and streams bytes back to client
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Client              │  5. Decode audio chunk (Ogg/Vorbis, AAC, Opus)
   │                      │  6. Send to audio device → MUSIC PLAYS
   │                      │  7. Prefetch next chunks / next track
   └──────────┬───────────┘
              │
              │  (in parallel, async)
              ▼
   ┌──────────────────────┐
   │  API Gateway         │  8. POST /events with play event
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Kafka               │  9. Publish "track_played" event
   └──────────┬───────────┘
              │
       ┌──────┴───────────┬──────────────────┐
       ▼                  ▼                  ▼
┌────────────┐   ┌──────────────────┐  ┌──────────────────┐
│ Royalty    │   │ ML Feature Store │  │ Analytics        │
│ Counter    │   │ (update user     │  │ (Spotify for     │
│ (pay artists)│  │  profile)        │  │  Artists, charts)│
└────────────┘   └──────────────────┘  └──────────────────┘
```

### Step-by-step narrative

1. **Tap.** The client already has the track metadata (title, duration, file URLs) from when the playlist was fetched.
2. **Request chunk.** The client asks the nearest edge node for the first chunk of the audio file (chunk size ~a few seconds of audio). Edge selection is by geographic proximity and current load.
3. **Cache check.** If the edge has the chunk cached (hot track), it serves immediately. Otherwise, the edge pulls the file from origin object storage (S3/GCS), caches it, and serves.
4. **Stream.** Chunks flow back to the client over HTTPS (or historically RTP/UDP for the P2P path).
5. **Decode & play.** The client decodes the codec and sends PCM samples to the device audio subsystem. Music starts within ~1 second of the tap.
6. **Prefetch.** While song N plays, the client requests the first chunks of song N+1 (and maybe N+2) so there's no gap between tracks.
7. **Adaptive bitrate.** If the network degrades, the client requests lower-bitrate chunks for the rest of the track to avoid rebuffering. If the network improves, it switches back up.
8. **Event emission.** In parallel with playback, the client emits a `track_played` event to the API, which publishes to Kafka.
9. **Downstream consumers.** Kafka fans out to:
   - **Royalty counter** — increments play count for the track's rights-holders.
   - **ML feature store** — updates the user's listening profile (which feeds future recommendations).
   - **Analytics** — drives charts, Spotify for Artists dashboards, internal metrics.

**Key insight:** the audio bytes flow over the **streaming path** (edge → client), while metadata and events flow over the **API path** (client → API gateway → services). Separating these lets each scale independently.

---

## 6. Scaling Strategy

### Audio streaming scale

- **Multi-CDN strategy.** Spotify uses multiple CDNs (their own edge + commercial CDNs) to balance load and provide redundancy.
- **Edge caching.** Hot tracks (top charts) are cached at edge nodes globally. Long-tail tracks may require an origin fetch but are requested far less often.
- **Client-side caching.** The app keeps recently played audio on-device, so replaying a song often doesn't hit the network at all.
- **Prefetching.** Anticipating the next track eliminates perceived latency at track boundaries.

### Metadata scale

- Stateless microservices scale horizontally behind load balancers.
- Heavy caching (Redis) in front of the catalog DB — the top tracks are requested constantly.
- Read replicas for the catalog database.

### Event pipeline scale

- Kafka partitions events by `user_id` (or `track_id`) to spread load.
- Consumers scale independently per topic — the royalty counter doesn't need to keep up with the ML pipeline.
- Kafka acts as a buffer during traffic spikes (New Music Friday, album drops).

### Data scale

- Cassandra scales linearly by adding nodes; consistent hashing distributes data.
- Object storage (S3/GCS) is effectively infinitely scalable for audio blobs.
- Data lake (S3) stores years of play events for ML training — petabytes, queried by Spark.

### Multi-region

- Metadata services run in multiple regions with async replication.
- Audio edges are globally distributed (the user's nearest edge is typically < 50ms away).
- Recommendation models are trained centrally but deployed to regional inference servers.

### Failure handling

- **Audio path failure:** client retries with a different CDN/edge. If all fail, it falls back to cached audio or shows an error.
- **Metadata failure:** the client can keep playing queued tracks even if metadata services are down (state is local).
- **Offline mode (premium):** downloaded tracks play without any network — the client has the audio files locally.

---

## 7. Tech Stack

Spotify has been candid about their stack over the years (check the Spotify Engineering Blog and their open-source releases).

| Layer | Technology |
|---|---|
| Clients | iOS (Swift), Android (Kotlin), Desktop (historically C++/Qt; now largely web-based), Web (React) |
| Backend services | Mostly **Java** (and **Scala**) on the JVM; some Python (ML), Go |
| RPC framework | **gRPC** / Protocol Buffers (with custom HTTP APIs for clients) |
| Service mesh / discovery | Apollo (Spotify's in-house framework, open-sourced), now largely Kubernetes + Istio |
| Primary databases | **PostgreSQL** (users, billing, catalog) |
| Wide-column store | **Cassandra** (play history, playlists) |
| Cache | **Redis** |
| Search | **Elasticsearch** (with custom Lucene analyzers) |
| Object storage | **Google Cloud Storage** (Spotify migrated from AWS to GCP in 2016–2018) |
| Event bus | **Apache Kafka** (with internal Pub/Sub-style abstractions) |
| Big data | **Apache Beam** + **Scio** (Spotify's Scala API for Beam), BigQuery, Dataflow |
| ML | Python (TensorFlow, PyTorch), custom recommendation systems |
| Orchestration | Google Kubernetes Engine (GKE) |
| Observability | Prometheus, custom tooling |

Notable in-house / open-source systems:

- **Apollo** — Spotify's Java service framework.
- **Scio** — Scala API for Apache Beam (open-sourced).
- **Luigi** — Python pipeline orchestration (open-sourced; predecessor to Airflow in many ways).
- **Hermes** — internal pub/sub (not to be confused with other things named Hermes!).
- **Echo Nest** — acquired 2014; their audio analysis tech powers Spotify's content-based recommendations.

---

## 8. How YOU Can Build a Simplified Version

A weekend Spotify clone teaches the core concepts. Scope it down hard.

### Scope (MVP)

- Upload a catalog of songs (admin only).
- User login.
- Search songs.
- Play a song (stream audio).
- Create a playlist.
- "Recommended" section (basic: top-played songs).

Skip: recommendations ML, podcasts, social, offline mode, adaptive bitrate.

### Minimal stack

```
┌────────────┐   ┌──────────────────┐   ┌──────────┐   ┌─────────┐
│  React /   │──▶│  Node.js / Flask │──▶│  Redis   │──▶│ Postgres│
│  Next.js   │   │  API             │   │  cache   │   │  (data) │
└────────────┘   └──────────────────┘   └──────────┘   └─────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │  Object Store│   (S3 / MinIO for audio files)
                   └──────────────┘
```

### Data model (Postgres)

```sql
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE artists (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE tracks (
    id          BIGSERIAL PRIMARY KEY,
    artist_id   BIGINT REFERENCES artists(id),
    title       TEXT NOT NULL,
    duration_s  INT NOT NULL,
    audio_url   TEXT NOT NULL,    -- e.g., S3 URL or /audio/<id>.mp3
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE playlists (
    id      BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    name    TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE playlist_tracks (
    playlist_id BIGINT REFERENCES playlists(id),
    track_id    BIGINT REFERENCES tracks(id),
    position    INT,
    PRIMARY KEY (playlist_id, track_id)
);
```

### Streaming the audio (Node.js example)

```js
const express = require('express');
const fs = require('fs');
const app = express();

app.get('/audio/:trackId', async (req, res) => {
  const track = await db.getTrack(req.params.trackId);
  const range = req.headers.range;  // HTTP Range header for chunked streaming
  const filePath = `/audio/${track.audio_url}`;

  if (range) {
    // Partial content (chunked) - what enables seek + fast start
    const stat = fs.statSync(filePath);
    const fileSize = stat.size;
    const parts = range.replace(/bytes=/, '').split('-');
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
    const chunkSize = end - start + 1;
    const stream = fs.createReadStream(filePath, { start, end });
    res.writeHead(206, {
      'Content-Range': `bytes ${start}-${end}/${fileSize}`,
      'Accept-Ranges': 'bytes',
      'Content-Length': chunkSize,
      'Content-Type': 'audio/mpeg',
    });
    stream.pipe(res);
  } else {
    // Full file
    res.writeHead(200, { 'Content-Type': 'audio/mpeg' });
    fs.createReadStream(filePath).pipe(res);
  }
});
```

The magic is **HTTP Range requests**. The client requests `Range: bytes=0-1048575` to get the first MB; the server responds `206 Partial Content`. This lets the client start playing as soon as the first chunk arrives, and seek to any position by requesting a different range. This is exactly how HTTP-based audio streaming works at scale.

### Frontend audio element

```html
<audio src="/audio/123" controls></audio>
```

That's it. The browser handles buffering, decoding, and playback. For more control (gapless playback, crossfading), you'd use the Web Audio API.

### Put it behind a CDN

For any real traffic, put CloudFront/Cloudflare in front of `/audio/*`. The CDN caches audio chunks at edge nodes so your origin server isn't hit for every play.

### Stretch goals

1. **Search** → spin up Meilisearch or Typesense, index tracks on insert.
2. **Playlists** → implement CRUD; store track order in `playlist_tracks.position`.
3. **Recommendations** → start simple: "users who played X also played Y" (collaborative filtering on your play_history table). Add audio features (tempo, energy) for content-based similarity.
4. **Adaptive bitrate** → encode each track at 3 bitrates (using FFmpeg); client requests based on `navigator.connection.effectiveType`.
5. **Real-time collaborative playlists** → WebSocket (Socket.io) for live updates.

### Deployment

- **Frontend:** Vercel / Netlify (free).
- **API:** Railway / Render / a VPS.
- **DB:** Supabase / Neon (managed Postgres).
- **Audio:** Cloudflare R2 or AWS S3 + CloudFront.
- **Total cost for a demo:** $0–$5.

---

## 9. Key Design Decisions & Trade-offs

### Decision 1: Hybrid P2P (historical) vs pure client-server

**Early Spotify (2008–~2014):** desktop clients acted as P2P peers — if a chunk of a song was on your disk, you'd serve it to nearby users. This drastically reduced Spotify's bandwidth costs.

**Why abandoned:** Mobile devices can't reliably be P2P peers (battery, NAT, background limits). The world went mobile-first, and commercial CDNs got cheap. P2P added complexity for diminishing returns.

**Trade-off:** P2P traded client complexity + unpredictability for cheaper bandwidth. Server/CDN-only trades money for simplicity and reliability. Spotify chose the latter as they scaled globally.

### Decision 2: Separate audio path from metadata path

Audio streaming is high-throughput, latency-sensitive, cache-friendly. Metadata is request/response, low-volume, frequently-changing. Mixing them would force the audio servers to also do DB lookups, and force metadata servers to handle huge byte transfers. **Separating them** lets each scale and fail independently.

### Decision 3: Cassandra for play history and playlists (not Postgres)

- **Write volume:** billions of play events/day. Postgres would need aggressive sharding.
- **Data shape:** time-series, append-mostly. Cassandra is optimized for exactly this.
- **Availability:** Cassandra is eventually consistent and highly available. A briefly-stale play count is fine.
- **Trade-off:** you give up transactions and rich queries. For play history, that's acceptable.

### Decision 4: Pre-compute recommendations nightly (batch) vs real-time

Discover Weekly is a **batch job** that runs overnight. Why?

- It's a heavy computation (collaborative filtering over billions of playlists).
- Users don't expect it to update minute-by-minute.
- Batch is far cheaper per computation than real-time inference.

For the **home feed** and **radio**, Spotify uses real-time inference with pre-trained models + cached features — more expensive but more responsive.

### Decision 5: Multi-bitrate encoding vs single file

- Encoding each track at 3–4 bitrates multiplies storage by 3–4×.
- But it enables **adaptive bitrate streaming**, which is critical for mobile users on flaky networks.
- **Trade-off:** storage cost for UX. Worth it.

### Decision 6: Own edge nodes vs commercial CDN

Spotify historically ran their own edge servers in rented data centers worldwide. This gave them control and (at scale) cost advantages. The migration to Google Cloud (2016–2018) moved much of this to Google's CDN. Today it's a **hybrid**: some own-edge, some commercial CDN.

**Trade-off:** owning gives control and potentially lower cost at huge scale; commercial CDN gives elasticity and less operational burden.

### Decision 7: Lossless / Hi-Fi tier

Adding lossless audio (FLAC) dramatically increases storage and bandwidth per stream (a lossless track is ~30–50MB vs ~5MB for 320kbps MP3). Spotify delayed their lossless tier for years (as of 2026, "Spotify HiFi" is still limited in availability) — the cost/UX trade-off is non-trivial at their scale.

---

## 10. Common Interview Questions

**Q1: How would you design Spotify?**
A: Start by separating the two core flows: (1) metadata/API (search, catalog, playlists) and (2) audio streaming. Metadata uses standard request/response with caching; audio uses chunked streaming over HTTP Range requests with a CDN at the edge. Use Postgres for the catalog and billing, Cassandra for play history and playlists (write-heavy, time-series), Redis for hot cache and sessions, Kafka to capture every play event, and an offline Spark/Beam pipeline to train recommendation models.

**Q2: How do you stream audio to millions of concurrent listeners?**
A: Multi-CDN strategy with edge caching. Audio files are stored in object storage (S3/GCS) and chunked. Edge nodes cache hot chunks; clients request the nearest edge by geography. Clients also cache recently played audio locally and prefetch the next track. Adaptive bitrate (multiple encodings per track) handles variable network quality.

**Q3: How does Spotify recommend music (Discover Weekly)?**
A: Three complementary approaches. (1) Collaborative filtering: "users who put A and B in the same playlist find them similar" — co-occurrence over billions of playlists. (2) Audio analysis: every track is processed by ML to extract features (tempo, key, energy, danceability) — this solves the cold-start problem for new tracks. (3) Sequence models / embeddings: modern DL models over listening history. Discover Weekly is a nightly batch job combining all three, with diversity constraints.

**Q4: Why use Cassandra for play history instead of Postgres?**
A: Play history is a massive write-heavy time-series (billions of events/day). Cassandra's partitioned, append-optimized, eventually-consistent model fits perfectly: partition by `(user_id, day_bucket)` for fast range scans, TTL to auto-expire old data, add nodes linearly. Postgres would need aggressive sharding and would be more expensive per write.

**Q5: Walk through what happens when you press play.**
A: (See §5.) The client already has the track's audio URL. It requests the first chunk from the nearest CDN edge. If cached, edge serves immediately; otherwise it fetches from origin object storage, caches, and serves. Client decodes and plays within ~1s. In parallel, the client emits a `track_played` event to Kafka, which fans out to royalty accounting, ML feature store, and analytics. The client prefetches the next track to avoid gaps.

**Q6: How do you handle adaptive bitrate?**
A: Encode each track at multiple bitrates (e.g., 96/160/320 kbps + lossless). The client monitors network conditions (packet loss, throughput) and requests the appropriate bitrate chunk. On degradation, it downshifts to avoid rebuffering; on improvement, it upshifts. This is similar to HLS/DASH adaptive streaming.

**Q7: How would you handle the cold-start problem for new tracks?**
A: Content-based recommendations using audio features. Even before a track has any plays, the audio analysis pipeline extracts tempo, key, energy, danceability, etc. New tracks can be recommended to users who like songs with similar features. As plays accumulate, collaborative filtering takes over.

**Q8: How do you count plays for royalties at scale?**
A: Every play emits a Kafka event. A stream processor (e.g., Flink or Spark Streaming) aggregates play counts per track per time window. These counts are stored and periodically reconciled into royalty payout reports. Using Kafka + stream processing decouples counting from playback and handles spikes (New Music Friday) gracefully.

**Q9: How would you implement search across 100M tracks?**
A: Elasticsearch cluster with the catalog indexed by title, artist, and lyrics (where licensed). Shard the index across many nodes; replicate for throughput. Use TF-IDF/BM25 ranking boosted by popularity. Add typo tolerance (Levenshtein) and "did you mean" suggestions via a separate n-gram index. Cache hot queries in Redis.

**Q10: How do you handle a track drop that causes a 100× traffic spike?**
A: Kafka absorbs the spike — events queue up if consumers fall behind. CDN caches absorb most audio requests (hot tracks stay cached). Stateless services autoscale. Recommendation/analytics pipelines can lag briefly; user-facing features degrade gracefully (e.g., Discover Weekly updates a few hours late). The key is decoupling via the event bus so no single component becomes a bottleneck.

---

## 11. Further Reading

- Spotify Engineering Blog (engineering.atspotify.com) — many posts on recommendations, Cassandra usage, GCP migration.
- "The Spotify Experience" paper, and "Music Streaming at Scale" talks.
- Echo Nest acquisition writeups (2014) — basis of audio analysis.
- Scio (Spotify's Scala API for Apache Beam) — github.com/spotify/scio.
- Luigi — github.com/spotify/luigi.
- *Designing Data-Intensive Applications* (Kleppmann) — chapters on replication, partitioning, batch/stream processing.
- *Machine Learning Systems* — for the recommendation pipeline patterns.

---

*Last updated: July 2026. Numbers are approximate and based on public reporting / industry estimates — treat them as orders of magnitude, not exact figures.*

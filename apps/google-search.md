# Google Search — System Design

> **Reader's note:** This is a deep, standalone walkthrough. Read top to bottom and you will understand how Google Search works end-to-end — the crawler, the index, the ranking, the serving, and how to build a simplified version yourself. No buzzwords without explanation.

---

## 1. Overview & Scale Numbers

Google Search is the largest information retrieval system ever built. A user types a query, and within ~300 milliseconds, Google returns a ranked list of the most relevant pages from an index of hundreds of billions of documents. The scale is almost beyond comprehension.

### Why is this hard?

Three hard problems combine:

1. **Scale of the web.** The web is effectively infinite and constantly changing. Pages are created, modified, and deleted every second. Google must discover, download, store, and index all of it.
2. **Relevance at scale.** For any query, there may be millions of matching documents. Ranking them by true relevance — in milliseconds — is one of the hardest ML/IR problems in the world.
3. **Latency under load.** Billions of queries per day, each answered in under a second, from index shards distributed globally.

### Real-world scale (publicly reported / industry estimates)

| Metric | Approximate value |
|---|---|
| Indexed pages | ~100 billion+ (the "known web" is larger but much is low-quality) |
| Search queries/day | ~8–9 billion |
| Search queries/second (peak) | ~100,000+ |
| Avg query latency | ~300–500 ms (end-to-end, including network) |
| Size of the index | ~100,000,000 GB (100 PB) — this is the famous Google number |
| Web crawled/day | tens of PB of new/changed content |
| Data centers | 20+ globally |
| Languages supported | 150+ |
| URLs in crawl frontier | ~100 trillion+ |
| Freshness targets | seconds–minutes for breaking news |

**Storage math (back-of-envelope):**

- 100B pages × avg 500KB compressed per page = ~50 PB of raw content.
- The index (inverted index + metadata) is larger — ~100 PB.
- Caching, replication, and redundancy multiply this further.

**Latency budget breakdown (typical query):**

- Network round-trip to Google: ~30–80 ms.
- Query parsing + understanding: ~10 ms.
- Index lookup + candidate retrieval: ~50–100 ms.
- Ranking (ML models): ~100–200 ms.
- Result assembly + rendering: ~20 ms.
- Total: **~300 ms** from query to rendered results.

Google's famous bar is that every millisecond matters. They've measured that slowing results by even 400ms reduces user satisfaction and query volume.

---

## 2. High-Level Architecture

Google Search has two major subsystems that are almost entirely separate:

1. **The Backend (Offline):** Crawling the web, building the index, training ranking models. This is a massive batch/data pipeline. Users never see it; it runs continuously in the background.
2. **The Frontend (Online):** Receiving a query, looking up the index, ranking, returning results. This is the user-facing, latency-critical path.

```
╔════════════════════════●══════════════════════════════════════════════╗
║                        OFFLINE PIPELINE (backend)                      ║
║                                                                        ║
║   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐         ║
║   │  Crawl   │───▶│  Parsing │───▶│  Link    │───▶│  Index   │         ║
║   │  Bot     │    │  / Link  │    │  Analysis│    │  Build   │         ║
║   │(Googlebot│    │  Extract │    │ (PageRank│    │ (Inverted│         ║
║   │          │    │          │       │   etc.)  │    │   Index) │         ║
║   └────┬─────┘    └──────────┘    └──────────┘    └────┬─────┘         ║
║        │                                                │               ║
║        ▼                                                ▼               ║
║   ┌──────────┐                              ┌──────────────────────┐    ║
║   │  URL     │  (crawl scheduling:          │   Index Shards       │    ║
║   │  Frontier│   which URLs to visit next,   │   (distributed across│    ║
║   │          │   how often)                  │   thousands of       │    ║
║   └──────────┘                               │   machines)          │    ║
║                                              └──────────┬───────────┘    ║
║                                                         │                ║
╚═════════════════════════════════════════════════════════╪════════════════╝
                                                           │
                                                           │ index pushed
                                                           │ to serving tier
                                                           ▼
╔════════════════════════●══════════════════════════════════════════════╗
║                        ONLINE PIPELINE (frontend)                      ║
║                                                                        ║
║                        ┌──────────────────────┐                         ║
║                        │       USER           │                         ║
║                        │   (types a query)    │                         ║
║                        └──────────┬───────────┘                         ║
║                                   │ HTTPS                               ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │  EDGE / GOOGLE FRONT │  TLS, DNS, load balance ║
║                        │  (Google's edge net) │                         ║
║                        └──────────┬───────────┘                         ║
║                                   │                                     ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │  Query Understanding │  spell correct, synonym ║
║                        │  (parse, intent)     │  expansion, intent detect║
║                        └──────────┬───────────┘                         ║
║                                   │                                     ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │  Index Lookup        │  query the index shards ║
║                        │  (candidate retrieval│  for matching documents ║
║                        │   — thousands of     │                         ║
║                        │   machines in parallel│                        ║
║                        └──────────┬───────────┘                         ║
║                                   │                                     ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │  Ranking             │  ML models score and    ║
║                        │  (Top-N selection)   │  order candidates       ║
║                        └──────────┬───────────┘                         ║
║                                   │                                     ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │  Result Assembly     │  snippets, knowledge    ║
║                        │                      │  panels, images, news   ║
║                        └──────────┬───────────┘                         ║
║                                   │                                     ║
║                                   ▼                                     ║
║                        ┌──────────────────────┐                         ║
║                        │       USER           │  rendered results page  ║
║                        │   (sees results)     │                         ║
║                        └──────────────────────┘                         ║
╚════════════════════════════════════════════════════════════════════════╝
```

### Walking the diagram

**Offline:**

1. **Crawl:** Googlebot downloads pages from the web.
2. **Parse & Extract:** extract text, links, metadata from downloaded pages.
3. **Link Analysis:** compute authority signals (PageRank and successors) from the link graph.
4. **Index Build:** build the inverted index — the data structure that maps terms to documents.
5. **Push to serving:** the built index is distributed to thousands of index-serving machines globally.

**Online:**

1. User queries → Google's edge network.
2. **Query Understanding** corrects spelling, expands synonyms, detects intent.
3. **Index Lookup** queries the distributed index shards for candidate documents.
4. **Ranking** scores candidates with hundreds of signals + ML models.
5. **Result Assembly** generates snippets, knowledge panels, images.
6. Results returned to user in ~300ms.

---

## 3. Detailed Component Breakdown

### 3.1 Googlebot (the Crawler)

Googlebot is a massively parallel web crawler. Its job: discover URLs, download their content, and hand them to the indexing pipeline.

**Crawl process:**

```
   ┌──────────────────┐
   │  URL Frontier    │  ← priority queue of URLs to crawl
   │  (prioritized)   │
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  DNS Resolution  │  ← resolve hostname to IP (cached heavily)
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  HTTP Fetcher    │  ← GET the page (respect robots.txt, crawl-delay)
   │                  │     render JavaScript (Web Rendering Service)
   └────────┬─────────┘
            │
            ▼
   └──────────────────┐
   │  Parser          │  ← extract text, links, metadata
   │  (extract links  │
   │   → add to       │
   │   frontier)      │
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  Content Store   │  → handoff to indexing pipeline
   └──────────────────┘
```

**Key design points:**

- **URL Frontier:** a priority queue. Important sites (news, high-PageRank) are crawled more frequently. New URLs are added as the crawler discovers links. This is a giant graph traversal (BFS/DFS) across the entire web.
- **Politeness:** the crawler respects `robots.txt` (per-site rules about what can be crawled) and rate limits its requests to any single server so it doesn't hammer small sites.
- **Freshness:** news sites are crawled every few minutes; static sites every few weeks. The crawl scheduler optimizes for "maximum freshness per unit of crawl budget."
- **JavaScript rendering:** modern pages are JS-heavy (React, Vue). Googlebot uses a rendering service (essentially a headless Chrome) to execute JS and get the final rendered HTML. This is expensive, so it's done in a deferred second pass.
- **Distributed:** thousands of crawl workers run in parallel, each handling a subset of the frontier. Coordination ensures no two workers crawl the same URL simultaneously.

### 3.2 Parser / Link Extractor

Takes raw HTML and extracts:

- **Visible text** (stripped of markup) → feeds the inverted index.
- **Links** (outgoing `<a href>` tags) → feeds the link graph and the URL frontier (new URLs to crawl).
- **Metadata:** `<title>`, `<meta description>`, Open Graph tags, structured data (`schema.org` JSON-LD).
- **Anchor text:** the text of links pointing *to* a page is a strong relevance signal ("if many people link to X with the text 'best laptops', X is probably about best laptops").

### 3.3 Link Analysis (PageRank and successors)

PageRank is Google's famous algorithm — the original breakthrough that made Google better than earlier search engines. Conceptually:

> A page is important if many other important pages link to it.

Mathematically, PageRank models a "random surfer" who randomly clicks links. The probability of landing on any given page is its PageRank. Computed iteratively over the link graph:

```
   PR(A) = (1-d)/N + d * Σ ( PR(Ti) / C(Ti) )
                     i

   where:
   PR(A)  = PageRank of page A
   d      = damping factor (~0.85)
   N      = total number of pages
   Ti     = pages that link to A
   C(Ti)  = outbound link count of Ti
```

**Intuition:** each page distributes its PageRank equally among the pages it links to. A page linked to by many high-PR pages gets a high PR itself.

Modern Google uses many more signals (semantic relevance, user behavior, freshness, etc.), but the link-graph authority concept remains foundational.

### 3.4 Index Build (the Inverted Index)

This is the core data structure of any search engine.

**What is an inverted index?**

A normal index (like a book's index) maps pages → terms. An **inverted index** reverses this: it maps **terms → pages**.

```
   Forward index (document → terms):       Inverted index (term → documents):
   ┌────────────┐                          ┌────────────┐
   │ doc 1:     │                          │ "apple":   │ → [doc1, doc5, doc12, ...]
   │  apple,    │                          │ "banana":  │ → [doc2, doc5, doc8, ...]
   │  pie,      │   build by               │ "pie":     │ → [doc1, doc9, ...]
   │  recipe    │  ◀────────               │ "recipe":  │ → [doc1, doc3, doc9, ...]
   │ doc 2:     │                          │ ...        │
   │  banana,   │                          └────────────┘
   │  bread
   └────────────┘
```

To find documents containing "apple pie recipe," the index lookup is:

1. Look up "apple" → [doc1, doc5, doc12, ...]
2. Look up "pie" → [doc1, doc9, ...]
3. Look up "recipe" → [doc1, doc3, doc9, ...]
4. **Intersect** the lists → doc1 (and any others in all three).

This intersection is extremely fast because the posting lists are sorted by doc ID and can be merged in linear time.

**Posting list structure:**

Each entry in a posting list isn't just a doc ID — it also stores:

- **Term frequency** (how many times the term appears in the doc) — a relevance signal.
- **Positions** (word offsets) — enables phrase queries ("apple pie" as a phrase, not just both words anywhere).
- **Formatting info** (bold, heading, title) — terms in `<h1>` or `<title>` are weighted higher.

**Scale of the index:**

- 100B documents, millions of distinct terms.
- A single machine cannot hold this. The index is **sharded** across thousands of machines.
- Typical sharding: by **document ID** (each shard holds the full index for a subset of documents). A query goes to all shards in parallel; each returns its top candidates; a merger combines them.

```
   Query "apple pie"
        │
        ├──▶ Shard 1 (docs 0-1B)      → top candidates: [doc42, doc100]
        ├──▶ Shard 2 (docs 1B-2B)     → top candidates: [doc1.5B, doc1.8B]
        ├──▶ Shard 3 (docs 2B-3B)     → top candidates: [doc2.1B]
        ...
        └──▶ Shard N (docs (N-1)B-NB) → top candidates: [...]
                            │
                            ▼
                   ┌────────────────┐
                   │  Merger        │  → global top 1000 candidates
                   │  (combines per-│     → passed to Ranker
                   │   shard results)│
                   └────────────────┘
```

### 3.5 Query Understanding

Before hitting the index, the query is processed:

- **Spell correction:** "aple pie" → "apple pie". Trained on historical query logs and document corpus.
- **Stemming/Lemmatization:** "running" → "run", "mice" → "mouse".
- **Synonym expansion:** "car" → also match "automobile", "vehicle". Google's synonym system is famously sophisticated.
- **Intent detection:** is this a navigational query (user wants a specific site, e.g., "facebook"), transactional ("buy iPhone"), or informational ("how to tie a tie")? Intent drives which features and results are shown.
- **Query expansion:** add related terms to improve recall.

### 3.6 Ranking

The ranking stage takes ~1000 candidate documents (from index lookup) and scores them to produce the final ordered list.

**Hundreds of signals** contribute to the score. Major categories:

| Signal category | Examples |
|---|---|
| **Query-document relevance** | Term frequency, term position (title? heading? body?), phrase match, exact match |
| **Document quality / authority** | PageRank, link count, domain authority, spam score |
| **Freshness** | When was the page last updated? Is the query time-sensitive ("election results")? |
| **User context** | Location (a "pizza" query in Tokyo vs. New York), language, search history, personalization |
| **User behavior** | Click-through rate for this query-doc pair (do users who search this click this result?), dwell time, bounce rate |
| **Document features** | Page speed, mobile-friendliness, HTTPS, ad density |
| **Semantic relevance** | BERT/MUM neural models that understand query and document meaning beyond keyword match |

**The ranking pipeline:**

```
   Candidate docs (from index)
        │
        ▼
   ┌──────────────────┐
   │  Fast Ranker     │  ← cheap features (term freq, PR) → narrow to ~100
   │  (heuristic /    │
   │   linear model)  │
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  ML Ranker       │  ← expensive features (neural models, user behavior)
   │  (Gradient Boosted│     → final ordering
   │   Trees / Neural │
   │   Net)           │
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  Result          │  ← assemble snippets, knowledge panels, images
   │  Assembly        │
   └──────────────────┘
```

Two-stage ranking: a fast first pass narrows thousands of candidates to ~100; an expensive ML model scores those 100 for the final order. This is a classic **candidate generation + ranking** pattern (also used in ads and recommendations).

**Modern neural ranking (BERT, MUM):**

Since ~2018, Google uses **BERT** (Bidirectional Encoder Representations from Transformers) to understand query and document semantics. BERT lets Google understand that "2019 brazil traveler to usa need a visa" is about a *Brazilian traveling to the US*, not the reverse — something keyword matching couldn't do.

**MUM** (Multitask Unified Model, ~2021) extends this across languages and modalities (text + images).

### 3.7 Result Assembly

The final step before returning results:

- **Snippets:** generate the text excerpt shown under each result. Dynamically generated to highlight query terms.
- **Knowledge Graph:** for entity queries ("Albert Einstein"), show a knowledge panel with structured facts.
- **Universal Search:** blend in results from verticals — images, videos, news, maps, shopping — into the main results.
- **Featured Snippets:** for question queries, extract a direct answer from a top result.
- **Autocomplete:** predict the rest of the query as the user types (a separate real-time model).

### 3.8 Caching

Google caches aggressively at multiple levels:

- **Result cache:** identical queries (and near-identical) return cached results. A huge fraction of queries are repeated.
- **Per-doc cache:** metadata and snippets for hot documents.
- **Crawled page cache:** the crawled version of a page is stored and reused until re-crawl.

Caching is why Google can serve billions of queries/day — a large fraction never hits the full ranking pipeline.

---

## 4. Data Model

Google Search doesn't have a traditional "database" in the relational sense. Its primary data structures are specialized:

### 4.1 The Inverted Index (the core)

Already covered in §3.4. It's a term-to-documents mapping, distributed across thousands of machines (index shards).

### 4.2 The Link Graph

A directed graph where nodes are pages and edges are links. Used for PageRank and authority computation. Stored as an adjacency list, processed in batch (the original "MapReduce" paper was about computing PageRank-like data over this graph).

### 4.3 The Document Repository

The raw downloaded pages, stored compressed. This is the source of truth for re-indexing. Stored in Google File System (GFS) / Colossus (its successor) — distributed filesystems optimized for large files.

```
   ┌─────────────────────────────────────────────────────┐
   │            DOCUMENT REPOSITORY (Colossus)           │
   │                                                    │
   │   doc_001: <html>...full HTML of page 1...</html>  │
   │   doc_002: <html>...full HTML of page 2...</html>  │
   │   ...                                              │
   │   doc_100B: <html>...</html>                       │
   └─────────────────────────────────────────────────────┘
```

### 4.4 The Knowledge Graph

A structured database of entities (people, places, things) and their relationships. Powers the Knowledge Panel. Launched ~2012. Contains billions of entities and relationships.

```
   Entity: Albert Einstein
   - type: person
   - born: 1879-03-14
   - died: 1955-04-18
   - field: physics
   - known_for: [theory of relativity, E=mc², ...]
   - spouse: [Mileva Marić, Elsa Einstein]
   - ...
```

### 4.5 User Data (for personalization)

- Search history (what you've searched before).
- Click history (what results you clicked).
- Location (approximate, for local results).
- Stored securely, with privacy controls. Drives personalization but is also a major privacy focus.

### 4.6 Why no relational DB?

Google Search's data doesn't fit relational patterns:

- The inverted index is a specialized data structure, not tables.
- The link graph is a graph, not tables.
- The document repository is blobs in a distributed filesystem.
- The Knowledge Graph is a graph/entity store.

Google built custom systems for each: **Bigtable** (sparse, distributed sorted map) for some metadata, **GFS/Colossus** for large files, **MapReduce/Flume** for batch processing, custom index-serving systems for the inverted index.

---

## 5. Request Flow — Searching Google

This is the online, user-facing path. Let's trace a query: **"best laptop 2026"**.

```
 User types "best laptop 2026" and hits Enter
              │
              ▼
   ┌──────────────────────┐
   │  Browser             │  1. DNS lookup for www.google.com
   │                      │  2. HTTPS GET /search?q=best+laptop+2026
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Google Edge Network │  3. Anycast DNS routes to nearest data center
   │  (Google Front)      │  4. TLS termination, load balancing
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Result Cache Check  │  5. Is this query (or near-variant) cached?
   │                      │     If yes → return cached results (skip steps 6-9)
   └──────────┬───────────┘
              │ cache miss
              ▼
   └──────────────────────┐
   │  Query Understanding │  6. Spell check, stemming, synonym expansion,
   │                      │     intent detection
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Index Lookup        │  7. Send query to all index shards in parallel
   │  (distributed)       │  8. Each shard returns its top-k candidates
   │                      │  9. Merger combines into ~1000 candidates
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Fast Ranker         │ 10. Score candidates with cheap features
   │                      │     Narrow to ~100
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  ML Ranker           │ 11. Score top-100 with expensive neural models
   │                      │     (BERT, user behavior, freshness, etc.)
   │                      │     Produce final ordering
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Result Assembly     │ 12. Generate snippets (highlight query terms)
   │                      │ 13. Add Knowledge Panel if entity detected
   │                      │ 14. Blend in images/news/videos (Universal Search)
   │                      │ 15. Insert ads (auction-based, separate pipeline)
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Return to User      │ 16. HTML/JSON response sent back
   │                      │ 17. Browser renders results page
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  User clicks a result│ 18. Click event logged (for ranking feedback)
   │                      │ 19. Target page loads
   └──────────────────────┘
```

### Step-by-step narrative

1–4. **Network & edge.** The browser resolves `www.google.com` via DNS (Google's DNS uses **anycast** — the same IP routes to the nearest Google data center). The request hits Google's edge network, terminates TLS, and is load-balanced to a search backend.
5. **Cache check.** A huge fraction of queries are repeated ("weather", "facebook", common searches). If cached, results return immediately — often in < 100ms. Cache hit rates are closely guarded but estimated at 30–50%+ for common queries.
6. **Query understanding.** "best laptop 2026" → stemming ("best" stays, "laptop" stays), synonym expansion (maybe "notebook", "ultrabook"), intent detection (transactional/informational). The year "2026" signals freshness intent.
7–9. **Index lookup.** The query terms are sent to all index shards in parallel. Each shard looks up its posting lists for "best", "laptop", "2026", intersects them, and returns its local top-k candidates. A merger combines per-shard results into a global candidate set (~1000 docs). This distributed fan-out is why the index must be sharded — no single machine can hold 100B documents' index entries.
10. **Fast ranker.** A cheap scoring pass (term frequency, PageRank, simple features) narrows ~1000 candidates to ~100. This must be fast because it touches many docs.
11. **ML ranker.** The top ~100 candidates are scored with expensive models: BERT for semantic relevance, user behavior features (click history), freshness, location. This produces the final ordering you see.
12–15. **Result assembly.** Snippets are generated by extracting text around query terms from each result. If the query matches an entity (e.g., a specific laptop model), a Knowledge Panel is added. Images, news, and videos are blended in (Universal Search). Ads are inserted via a **separate real-time auction** (Google Ads / AdWords).
16–17. **Return.** The assembled HTML/JSON is sent back. Total time: ~300ms.
18–19. **Feedback loop.** When you click a result, that click is logged. Over time, click data feeds back into ranking (results that get clicked more for a query are ranked higher — a reinforcement signal).

**Key insight:** the entire query path is a **distributed fan-out + merge**. The index is too large for one machine, so the query goes to thousands of machines in parallel, each returns a small result, and a merger combines them. This is the fundamental architecture of web search at scale.

---

## 6. Scaling Strategy

### Index sharding

The index is sharded by **document ID** across thousands of machines. A query fans out to all shards; each returns its top-k; a merger combines them. Adding capacity = adding shards.

### Replication for read throughput

Each shard is replicated N times. Queries are load-balanced across replicas. More replicas = more queries/sec.

### Caching at multiple levels

| Layer | What | Impact |
|---|---|---|
| Result cache | full results for common queries | skips entire pipeline |
| Per-doc cache | snippets/metadata for hot docs | speeds assembly |
| Crawl cache | crawled page content | avoids re-download |
| DNS cache | hostname → IP | avoids DNS lookup |

### Geo-distribution

Google has 20+ data centers globally. Anycast DNS routes each user to the nearest healthy data center. Each data center has a full copy of the index (or a regionally-relevant subset).

### MapReduce / Flume for batch processing

The offline pipeline (crawl processing, PageRank, index build) is a massive batch job. Google pioneered **MapReduce** for this (the original paper is about computing search index data). Modern Google uses **Flume** (their successor to MapReduce) and **Cloud Dataflow**.

```
   Crawl data (PB)  ──▶  MapReduce / Flume  ──▶  Index shards
                        (distributed batch)
```

### The "Caffeine" index architecture (~2010)

Historically, Google rebuilt the index in batches (every few weeks). **Caffeine** (2010) moved to a **continuous** indexing system: pages are added to the index as they're crawled, within seconds–minutes. This was a massive architectural shift enabling fresh news results.

### Handling query spikes

- Breaking news causes query spikes ("earthquake", election results).
- Caching absorbs repeated queries.
- Autoscaling of serving tiers.
- The index is read-only during serving (writes happen in the offline pipeline and are swapped in), so serving is extremely parallelizable.

---

## 7. Tech Stack

Google is famously secretive about exact current technology, but decades of papers, talks, and open-source releases give a clear picture.

| Layer | Technology |
|---|---|
| Crawler (Googlebot) | Custom distributed crawler, C++/Java |
| Rendering (JS) | Headless Chrome (Web Rendering Service) |
| Distributed filesystem | **GFS** (Google File System) → **Colossus** (successor) |
| Batch processing | **MapReduce** → **Flume** (Dataflow) |
| Big table / KV | **Bigtable** (sparse, distributed sorted map) |
| Distributed lock | **Chubby** (distributed lock service) |
| Index serving | Custom index-serving systems (C++ for performance) |
| Search ranking | C++ (for latency), TensorFlow / TPU for neural models |
| Knowledge Graph | Custom graph store + Bigtable |
| ML / Neural ranking | **TensorFlow**, **TPUs** (custom silicon) |
| Cluster orchestration | **Borg** (predecessor of Kubernetes) → **Kubernetes** (open-sourced) |
| Networking | **B4** (software-defined WAN), Jupiter (data center fabric) |
| Edge / DNS | Google Front, anycast DNS |
| Ads auction | Separate real-time bidding system (Google Ads) |
| Observability | Dapper (distributed tracing, open-sourced as... **OpenCensus** → OpenTelemetry) |

Notable in-house systems (many open-sourced or described in papers):

- **MapReduce** — batch processing (paper 2004; inspired Hadoop).
- **GFS** — distributed filesystem (paper 2003; inspired HDFS).
- **Bigtable** — distributed KV store (paper 2006; inspired HBase/Cassandra).
- **Chubby** — distributed lock service.
- **Borg** — cluster management (paper 2015; inspired Kubernetes).
- **Colossus** — GFS successor.
- **Dapper** — distributed tracing (inspired OpenTelemetry).
- **TensorFlow** — ML framework (open-sourced 2015).
- **TPU** — custom AI accelerator chip (Tensor Processing Unit).
- **BERT** — transformer model for language understanding (open-sourced 2018).
- **PageRank** — the original ranking algorithm (Brin & Page, 1998).

Google's stack is notable for **custom everything**: custom chips (TPU), custom networking (B4/Jupiter), custom databases (Bigtable/Spanner), custom cluster management (Borg). At Google's scale, off-the-shelf tools don't suffice.

---

## 8. How YOU Can Build a Simplified Version

A weekend search engine teaches the core concepts: crawl, index, rank, serve. You won't index the web, but you'll understand the architecture.

### Scope (MVP)

- Crawl a few thousand pages (start from a seed URL, follow links).
- Build an inverted index.
- Search by keywords (boolean AND).
- Rank by simple TF-IDF.
- Serve results via a web UI.

Skip: spell correction, neural ranking, Knowledge Graph, ads, personalization.

### Minimal stack

```
┌────────────┐   ┌──────────────────┐   ┌──────────┐   ┌─────────┐
│  React /   │──▶│  Python (Flask)  │──▶│  Whoosh  │   │ SQLite  │
│  plain HTML│   │  API             │   │  or      │   │ (crawl  │
│            │   │                  │   │Tantivy   │   │  meta)  │
└────────────┘   └──────────────────┘   │(inverted │   └─────────┘
                                         │  index)  │
                                         └──────────┘
```

### The crawler (Python)

```python
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import collections

seen = set()
queue = collections.deque(["https://example.com"])

while queue and len(seen) < 5000:
    url = queue.popleft()
    if url in seen:
        continue
    seen.add(url)
    try:
        resp = requests.get(url, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text(' ', strip=True)
        # Store the doc
        docs[url] = text
        # Extract links
        for link in soup.find_all('a', href=True):
            next_url = urljoin(url, link['href'])
            if urlparse(next_url).scheme in ('http', 'https'):
                queue.append(next_url)
    except Exception:
        continue
```

This is a basic BFS crawler. Real Googlebot adds: politeness (rate limits per domain), robots.txt, JavaScript rendering, priority scheduling, distributed coordination.

### Building the inverted index (Python, from scratch)

```python
from collections import defaultdict
import re

def tokenize(text):
    return re.findall(r'\b\w+\b', text.lower())

# Build index
inverted_index = defaultdict(list)   # term -> list of (doc_id, tf)
doc_lengths = {}

for doc_id, text in docs.items():
    tokens = tokenize(text)
    doc_lengths[doc_id] = len(tokens)
    tf = defaultdict(int)
    for token in tokens:
        tf[token] += 1
    for term, count in tf.items():
        inverted_index[term].append((doc_id, count))

# Search: intersect posting lists for all query terms
def search(query):
    terms = tokenize(query)
    if not terms:
        return []
    # Get posting lists
    postings = [set(doc_id for doc_id, _ in inverted_index.get(t, [])) for t in terms]
    # Intersect
    candidates = set.intersection(*postings) if postings else set()
    # Rank by TF-IDF
    N = len(docs)
    scored = []
    for doc_id in candidates:
        score = 0
        for t in terms:
            tf = sum(c for d, c in inverted_index.get(t, []) if d == doc_id)
            df = len(inverted_index.get(t, []))
            idf = math.log(N / (1 + df))
            score += tf * idf
        scored.append((doc_id, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:10]
```

This is a toy TF-IDF search engine. It demonstrates: tokenization, inverted index, posting-list intersection, and TF-IDF ranking. Real Google adds hundreds more signals and neural models on top.

### Use a real search library instead

For a better demo, use **Whoosh** (pure Python) or **Tantivy** (Rust, fast) or **Meilisearch** / **Typesense** (full-featured). They handle the index data structures efficiently.

```python
# Using Whoosh
from whoosh.index import create_in, open_dir
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import QueryParser

schema = Schema(path=ID(stored=True), content=TEXT)
ix = create_in("index_dir", schema)
writer = ix.writer()
for doc_id, text in docs.items():
    writer.add_document(path=doc_id, content=text)
writer.commit()

# Search
ix = open_dir("index_dir")
with ix.searcher() as searcher:
    query = QueryParser("content", ix.schema).parse("best laptop")
    results = searcher.search(query)
    for r in results:
        print(r['path'], r.score)
```

### Serving results (Flask)

```python
from flask import Flask, request, render_template

app = Flask(__name__)

@app.route('/search')
def search():
    q = request.args.get('q', '')
    results = my_search(q)   # your search function
    return render_template('results.html', query=q, results=results)

if __name__ == '__main__':
    app.run(debug=True)
```

### Deployment

- **Crawler + indexer:** run on a VPS or your laptop (crawling 5k pages takes minutes, not hours).
- **Search API:** Flask on Railway/Render.
- **Frontend:** simple HTML or React.
- **Total cost for a demo:** $0.

### Stretch goals

1. **PageRank** — build the link graph from your crawl, compute PageRank iteratively, blend into ranking.
2. **Snippets** — generate result excerpts by extracting text around query terms.
3. **Spell correction** — train on your query log (or use a library like `pyspellchecker`).
4. **TF-IDF → BM25** — upgrade your ranking function (BM25 is the standard improvement over TF-IDF).
5. **Sharding** — split your index across multiple processes/machines; merge results.

---

## 9. Key Design Decisions & Trade-offs

### Decision 1: Inverted index vs forward index

- **Forward index** (doc → terms): good for "what's in this doc?" but terrible for "which docs contain this term?"
- **Inverted index** (term → docs): perfect for the search use case (term → matching docs), enables fast posting-list intersection.

Every web search engine uses an inverted index. There is no real alternative at scale.

### Decision 2: Index sharding by doc ID vs by term

- **By doc ID:** each shard has the full index for a subset of docs. A query must hit all shards (fan-out), but each shard can return its top-k independently. Easy to parallelize.
- **By term:** each shard owns a range of terms. A query only hits shards for its terms. But popular terms become hot shards, and multi-term queries need cross-shard coordination.

Google shards by doc ID (the fan-out model). This balances load better and scales linearly.

### Decision 3: Batch indexing vs continuous indexing (Caffeine)

- **Batch (pre-2010):** rebuild the index every few weeks. Simpler, but results were stale. News wasn't fresh.
- **Continuous (Caffeine, 2010+):** index updated as pages are crawled, within minutes. Much harder engineering, but enables fresh news results.

Google moved to continuous indexing because freshness became a user expectation.

### Decision 4: Two-stage ranking (fast + ML)

- A single expensive ML ranker over all candidates would be too slow (1000 docs × neural model = seconds).
- Two stages: fast heuristic narrows to ~100; expensive ML scores those. This bounds latency while keeping quality.

### Decision 5: Custom silicon (TPUs)

- Running BERT/MUM at Google's query volume on GPUs would be prohibitively expensive and power-hungry.
- Google designed **TPUs** — custom chips optimized for neural network inference. This is a moat: competitors can't easily replicate the cost-efficiency.

**Trade-off:** billions in R&D for custom chips, but it pays off at Google's scale and is now a business (Google Cloud TPU).

### Decision 6: Personalization vs privacy

- Personalization (using your search/click history) improves relevance.
- But it raises privacy concerns and can create "filter bubbles."
- Google personalizes lightly (mostly location and language), less so on individual history. This is a deliberate trade-off.

### Decision 7: Caching everything

Google caches aggressively. The trade-off:

- **Pro:** massive latency and cost reduction. A cached result is nearly free to serve.
- **Con:** staleness. A cached result might be minutes old. For most queries this is fine; for breaking news it's not, so news queries bypass the cache or have short TTLs.

### Decision 8: Ads as a separate pipeline

Ads are auctioned in real-time (Google Ads / AdWords) via a separate system. They're blended into results at assembly time. This separation lets the ads system scale independently and ensures ad latency doesn't block organic results.

---

## 10. Common Interview Questions

**Q1: How would you design Google Search?**
A: Two subsystems. (1) Offline: crawl the web (Googlebot), parse and extract links/text, compute link analysis (PageRank), build an inverted index, distribute to serving machines. (2) Online: receive query, understand it (spell, synonyms, intent), look up the distributed index in parallel across shards, merge candidates, rank with a two-stage pipeline (fast heuristic + ML neural model), assemble results with snippets and knowledge panels, return in ~300ms. Cache aggressively at every level.

**Q2: How does Google crawl the web?**
A: A distributed crawler with a prioritized URL frontier. It starts with seed URLs, downloads pages, extracts links, and adds new URLs to the frontier. It respects robots.txt and rate-limits per domain (politeness). Priority scheduling ensures important sites are crawled more frequently. JavaScript rendering is done in a deferred second pass via headless Chrome. Thousands of workers run in parallel.

**Q3: What is an inverted index and why is it used?**
A: A mapping from terms to the documents that contain them (reverse of a forward index). Used because the core search operation is "find docs containing these terms" — the inverted index makes this a fast posting-list intersection. Posting lists are sorted by doc ID and can be merged in linear time. This is the foundational data structure of all web search engines.

**Q4: How does PageRank work?**
A: A page is important if many important pages link to it. Modeled as a random surfer clicking links; the probability of landing on a page is its PageRank. Computed iteratively over the link graph: PR(A) = (1-d)/N + d × Σ(PR(Ti)/C(Ti)). Pages linked to by high-PR pages get high PR. Modern Google uses many more signals, but link-based authority remains foundational.

**Q5: How do you rank billions of documents in milliseconds?**
A: Two-stage ranking. (1) Index lookup returns ~1000 candidates via posting-list intersection — this is fast because posting lists are sorted. (2) A fast ranker (cheap features: term frequency, PageRank) narrows to ~100. (3) An expensive ML ranker (BERT, user behavior, freshness) scores those 100 for the final order. The index is sharded across thousands of machines; the query fans out to all, each returns top-k, a merger combines them.

**Q6: How is the index distributed across machines?**
A: Sharded by document ID. Each shard holds the full index (all terms) for a subset of documents. A query is sent to all shards in parallel; each looks up its posting lists, intersects, and returns its top-k candidates. A merger combines per-shard results into a global candidate set. Shards are replicated for read throughput. Adding capacity = adding shards.

**Q7: How do you handle freshness (breaking news)?**
A: The Caffeine indexing system (2010+) indexes pages continuously as they're crawled, within minutes. News sites are crawled very frequently (every few minutes). For time-sensitive queries, freshness is a strong ranking signal. News results can also bypass the result cache (short TTL) so users see the latest.

**Q8: How do neural models (BERT/MUM) improve search?**
A: Traditional keyword matching can't understand semantics. "2019 brazil traveler to usa need a visa" — keyword matching might surface results about Americans traveling to Brazil. BERT understands the directional relationship: a Brazilian traveling to the US. MUM extends this across languages and modalities (text + images). These models are run on TPUs for latency and cost efficiency.

**Q9: How do you serve 8B queries/day at ~300ms latency each?**
A: Aggressive caching (30-50%+ of queries hit cache), geo-distributed data centers (anycast DNS routes to nearest), index sharding + replication for parallel read throughput, two-stage ranking to bound compute per query, custom silicon (TPUs) for neural inference. The index is read-only during serving (writes happen offline and are swapped in), so serving is embarrassingly parallel.

**Q10: How would you build a simplified version?**
A: Crawl a few thousand pages with a BFS scraper (Python + requests + BeautifulSoup). Build an inverted index (a dict mapping term → list of (doc_id, term_frequency)). Search by intersecting posting lists for query terms. Rank by TF-IDF or BM25. Serve via Flask. Use Whoosh or Tantivy for a real index library. Add PageRank by building the link graph and iterating. This teaches crawl → index → rank → serve, the full pipeline.

---

## 11. Further Reading

- **"The Anatomy of a Large-Scale Hypertextual Web Search Engine"** (Brin & Page, 1998) — the original Google paper. Still the best starting point.
- **"MapReduce: Simplified Data Processing on Large Clusters"** (Dean & Ghemawat, 2004).
- **"The Google File System"** (Ghemawat, Gobioff, Leung, 2003).
- **"Bigtable: A Distributed Storage System for Structured Data"** (Chang et al., 2006).
- **"Dapper, a Large-Scale Distributed Systems Tracing Infrastructure"** (2010).
- **"BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"** (2018).
- Google Research Blog (research.google/blog).
- *Introduction to Information Retrieval* (Manning, Raghavan, Schütze) — the canonical IR textbook. Free online.
- *Designing Machine Learning Systems* (Huyen) — for modern ML ranking pipelines.

---

*Last updated: July 2026. Numbers are approximate and based on public reporting / industry estimates — treat them as orders of magnitude, not exact figures.*

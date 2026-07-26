# Elasticsearch — The Complete Deep Dive

> Elasticsearch powers search for Wikipedia, Flipkart, Uber, Zomato, and GitHub. This guide covers how the inverted index works, how text analysis transforms words, how sharding distributes data, and how relevance scoring decides what you see first.

---

## Table of Contents

1. [What Problem Elasticsearch Solves](#the-problem)
2. [The Inverted Index — How Search Actually Works](#inverted-index)
3. [Text Analysis — Analyzers, Tokenizers, Filters](#analysis)
4. [The Relevance Score (TF-IDF & BM25)](#scoring)
5. [Architecture: Nodes, Clusters, Indices, Shards](#architecture)
6. [How Writes Work: Refresh, Flush, Merge](#writes)
7. [How Reads Work: Query Then Fetch](#reads)
8. [Mapping and Dynamic Mapping](#mapping)
9. [Aggregations — Analytics on Search Results](#aggregations)
10. [Performance Tuning](#tuning)
11. [Common Pitfalls](#pitfalls)
11. [How Real Companies Use Elasticsearch](#real-apps)
12. [How YOU Can Build This](#build)

---

<a id="the-problem"></a>
## What Problem Elasticsearch Solves

### Why You Can't Just Use SQL LIKE

```sql
-- Find products matching "running shoes"
SELECT * FROM products WHERE name LIKE '%running shoes%';
```

This SQL query has致命 problems:

```
1. EXACT SUBSTRING MATCH ONLY:
   "running shoes"       → match ✓
   "Running Shoes"       → NO MATCH (case-sensitive)
   "running shoe"        → NO MATCH (singular vs plural)
   "shoes for running"   → NO MATCH (word order differs)
   "red running sneakers"→ NO MATCH (shoes vs sneakers)

2. FULL TABLE SCAN:
   → Database checks EVERY ROW in the table
   → 10 million products → 10 million comparisons
   → Takes seconds (vs milliseconds with Elasticsearch)

3. NO RELEVANCE RANKING:
   → "Nike Air Zoom running shoes" and "shoehorn for running clips"
   → Both match. No way to say which is better.
   → Returns in arbitrary order (or by date).

4. NO TYPO TOLERANCE:
   → "runing shoes" (missing an 'n') → NO MATCH
```

### What Elasticsearch Does

```
User types: "running shoes"

Elasticsearch:
  1. Analyzes query: "running shoes" → ["run", "shoe"] (stemmed)
  2. Looks up inverted index:
     "run"  → [doc 5, doc 12, doc 89, doc 203, ...]
     "shoe" → [doc 5, doc 8, doc 12, doc 45, ...]
  3. Intersects: docs containing BOTH terms → [doc 5, doc 12, ...]
  4. Scores each document:
     - doc 5 ("Nike running shoes") → high score (both words in title)
     - doc 12 ("shoes for running") → high score
     - doc 203 ("running a marathon") → low score (no "shoe")
  5. Returns ranked results in ~10 milliseconds
```

### Full-Text Search vs Exact Match

```
EXACT MATCH (SQL/NoSQL):
  WHERE id = 123
  WHERE status = 'active'
  → Point lookup. Fast with B-tree index. Use a database.

FULL-TEXT SEARCH (Elasticsearch):
  WHERE text CONTAINS "comfortable running shoes for beginners"
  → Text analysis, stemming, synonyms, fuzzy matching, ranking
  → Use Elasticsearch.
```

---

<a id="inverted-index"></a>
## The Inverted Index — How Search Actually Works

### The Core Data Structure

**Analogy:** A book's index at the back. Instead of scanning every page for "photosynthesis", you look up "photosynthesis" in the index → page 47, page 89, page 203. Instant.

```
DOCUMENTS (what users store):

  Doc 1: "The red running shoes are amazing"
  Doc 2: "Blue shoes for marathon running"
  Doc 3: "Red car for sale"
  Doc 4: "Running is good for health"

STEP 1: ANALYZE each document (tokenize + normalize):
  Doc 1 → ["the", "red", "run", "shoe", "be", "amaze"]     (stemmed)
  Doc 2 → ["blue", "shoe", "for", "marathon", "run"]
  Doc 3 → ["red", "car", "for", "sale"]
  Doc 4 → ["run", "be", "good", "for", "health"]

STEP 2: BUILD INVERTED INDEX (term → document list):

  Term       Posting List (documents containing the term)
  ─────────  ──────────────────────────────────────────────
  "amaze"    [1]
  "be"       [1, 4]
  "blue"     [2]
  "car"      [3]
  "for"      [2, 3, 4]
  "good"     [4]
  "health"   [4]
  "marathon" [2]
  "red"      [1, 3]
  "run"      [1, 2, 4]
  "sale"     [3]
  "shoe"     [1, 2]
  "the"      [1]

STEP 3: QUERY "running shoes":
  → Analyze query: ["run", "shoe"]
  → Look up "run": [1, 2, 4]
  → Look up "shoe": [1, 2]
  → Intersection: [1, 2]  ← These docs match!
  → Score and rank: Doc 1 (both terms, title-like) > Doc 2

Time complexity: O(1) lookup + O(k) where k = matching docs
  vs O(N) full table scan in SQL
```

### Posting List Internals

```
A posting list is more than just document IDs:

  Term: "run"
  ┌────────────────────────────────────────────────────────┐
  │  Doc ID │ Term Freq │ Position  │  Offset             │
  │─────────┼───────────┼───────────┼─────────────────────│
  │  1      │    1      │   [2]     │  (4,10)             │
  │  2      │    1      │   [4]     │  (22,28)            │
  │  4      │    1      │   [0]     │  (0,7)              │
  └────────────────────────────────────────────────────────┘

  Term Frequency: How many times the term appears in the doc
  → More occurrences = more relevant

  Position: Where in the doc the term appears
  → Enables phrase queries: "running shoes" must be ADJACENT

  Offset: Character start/end position
  → Enables highlighting: "<em>running</em> shoes"
```

### Compression of Posting Lists

```
Posting lists can be huge (billions of docs for common words).
Elasticsearch compresses them using:

  Frame of Reference (FOR):
    → Store deltas (differences) instead of absolute values
    → Doc IDs: [1, 5, 8, 12, 15] → deltas: [1, 4, 3, 4, 3]
    → Deltas need fewer bits (smaller numbers)

  Roaring Bitmaps:
    → Partition doc IDs into 16-bit blocks
    → Dense blocks → compressed bitmap
    → Sparse blocks → sorted array
    → Best of both worlds: fast AND compact
```

---

<a id="analysis"></a>
## Text Analysis — Analyzers, Tokenizers, Filters

### What Happens During Analysis

Text analysis transforms raw text into searchable tokens. This happens at **two times**:
1. **At index time** — document text is analyzed before adding to the inverted index
2. **At query time** — the search query is analyzed the same way (usually)

```
INPUT: "The Quick-Brown Foxes jumped at 3PM!"

  ┌─────────────┐
  │  Tokenizer   │  Splits text into tokens
  │              │  ["The", "Quick", "Brown", "Foxes", "jumped", "at", "3PM"]
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │  Token Filter│  Modifies tokens
  │  (multiple)  │
  │              │
  │  Lowercase:  │  ["the", "quick", "brown", "foxes", "jumped", "at", "3pm"]
  │  Stop words: │  ["quick", "brown", "foxes", "jumped", "3pm"]  (removed "the", "at")
  │  Stemming:   │  ["quick", "brown", "fox", "jump", "3pm"]     (root form)
  │  Synonyms:   │  ["quick", "fast", "brown", "fox", "jump", "3pm"]  (added "fast" for "quick")
  │              │
  └──────────────┘
         │
  ┌──────▼──────┐
  │  Inverted    │  Tokens are added to the index
  │  Index       │
  └──────────────┘
```

### Character Filters (Pre-Tokenization)

```
HTML Strip Character Filter:
  Input:  "<p>Hello <b>World</b></p>"
  Output: "Hello World"
  → Removes HTML tags before tokenizing

Mapping Character Filter:
  Input:  "café"
  Output: "cafe"
  → Replace characters (e.g., accents to ASCII)

Pattern Replace Character Filter:
  Input:  "+1-555-123-4567"
  Output: "15551234567"
  → Regex-based replacement
```

### Tokenizers

```
STANDARD TOKENIZER (default):
  "Hello, World!" → ["Hello", "World"]
  → Splits on whitespace and punctuation
  → Good for most languages

WHITESPACE TOKENIZER:
  "Hello, World!" → ["Hello,", "World!"]
  → Splits on whitespace only (keeps punctuation)

KEYWORD TOKENIZER:
  "Hello World" → ["Hello World"]
  → No splitting! Entire input is one token
  → Used for exact match fields (IDs, categories)

NGRAM TOKENIZER:
  "cat" → ["c", "ca", "cat", "a", "at", "t"]
  → Generates substrings of length 1 to N
  → Used for partial matching ("cat" matches "category")
  → Warning: Generates enormous indexes!

EDGE NGRAM TOKENIZER:
  "cat" → ["c", "ca", "cat"]
  → Only prefixes (no interior substrings)
  → Used for autocomplete/typeahead
  → Much more efficient than full ngram
```

### Token Filters

```
LOWERCASE FILTER:
  "Hello WORLD" → ["hello", "world"]
  → Case-insensitive search

STOP FILTER:
  "the cat sat on the mat" → ["cat", "sat", "mat"]
  → Removes common words (the, a, an, is, of, in)
  → Saves index space, improves signal-to-noise

STEMMER FILTER:
  "running" → "run"
  "happily" → "happi"
  "boxes" → "box"
  → Reduces to root form
  → "running" matches "run", "runs", "runner"

SYNONYM FILTER:
  "iPhone" → ["iphone", "apple phone", "ios phone"]
  → Expands search to include synonyms
  → Configured per-index

ASCII FOLDING FILTER:
  "café" → "cafe"
  "München" → "Munchen"
  → Converts accented characters to ASCII
```

### Built-in Analyzers

```
STANDARD ANALYZER (default):
  → Standard tokenizer + lowercase + stop
  → Good for most text

SIMPLE ANALYZER:
  → Letter tokenizer (split on non-letters) + lowercase
  → "Hello-World 123" → ["hello", "world"]

WHITESPACE ANALYZER:
  → Whitespace tokenizer only (no lowercase!)
  → "Hello World" → ["Hello", "World"]

LANGUAGE ANALYZERS:
  → Language-specific stemming + stop words
  → "english", "french", "german", "hindi", etc.
  → english: "running" → "run", "mice" → "mouse"

KEYWORD ANALYZER:
  → No-op (entire input is one token)
  → For exact match fields

CUSTOM ANALYZER:
  → You choose: character filters + tokenizer + token filters
  → Maximum flexibility
```

---

<a id="scoring"></a>
## The Relevance Score — TF-IDF & BM25

When you search for "running shoes", Elasticsearch finds matching documents. But which document is the BEST match? That's what the relevance score determines.

### TF-IDF (Term Frequency × Inverse Document Frequency)

```
SCORE = TF × IDF

  TF (Term Frequency):
    How many times does the term appear in THIS document?
    "running running running" → TF = 3
    "running" → TF = 1
    → More occurrences = more relevant

  IDF (Inverse Document Frequency):
    How rare is this term across ALL documents?
    "the" appears in 99% of documents → IDF ≈ 0 (common, not informative)
    "marathon" appears in 0.1% → IDF = high (rare, very informative)

  TF-IDF:
    High score = term is frequent in THIS doc AND rare across all docs
    Low score = term is either rare in this doc OR common across all docs

  Example:
    Doc: "Running shoes for running enthusiasts who love running"
    Query: "running"
    TF = 3 (appears 3 times)
    IDF = 2.5 (appears in 10% of all docs)
    Score = 3 × 2.5 = 7.5
```

### BM25 (Best Matching 25) — Default Since Elasticsearch 5.0

```
BM25 improves on TF-IDF by adding SATURATION:

  TF-IDF Problem:
    A doc with "running" 100 times isn't 100x more relevant than a doc with 1.
    But TF-IDF gives it 100x the score.

  BM25 Saturation:
    Score increases rapidly at first, then levels off:

    Score
      ▲
      │           ╱─────── (saturation: more occurrences barely help)
      │         ╱
      │       ╱
      │     ╱
      │   ╱  (steep: first few occurrences matter most)
      │ ╱
      └──────────────────────► Term Frequency
           1  2  3  4  5  ... 100

  BM25 Formula (simplified):
    score = IDF × (TF × (k1 + 1)) / (TF + k1 × (1 - b + b × doc_len / avg_doc_len))

    k1 (default 1.2): Controls term frequency saturation
      → Higher k1 = slower saturation = TF matters more
      → Lower k1 = faster saturation = TF matters less

    b (default 0.75): Controls document length normalization
      → b = 1: Full normalization (long docs heavily penalized)
      → b = 0: No normalization (long docs not penalized)
      → Longer docs naturally have more term occurrences;
        BM25 normalizes for this so longer docs don't dominate.

  Example:
    Query: "running shoes"
    Doc A: "Nike running shoes" (short, both terms, TF=1 each)
    Doc B: "The complete guide to running, with sections on shoes, 
            socks, apparel, and everything related to running, 
            including barefoot running, trail running, marathon 
            running, and shoes for every type of running" (long, TF=5)

    BM25: Doc A scores HIGHER (shorter doc, terms are concentrated)
    TF-IDF: Doc B scores HIGHER (more term occurrences)
    BM25 matches human intuition better.
```

---

<a id="architecture"></a>
## Architecture: Nodes, Clusters, Indices, Shards

### Cluster Topology

```
┌──────────────────────────────────────────────────────────┐
│                    ELASTICSEARCH CLUSTER                  │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │  MASTER-ELIGIBLE  │  │  MASTER-ELIGIBLE  │             │
│  │  NODE 1           │  │  NODE 2           │             │
│  │                   │  │                   │             │
│  │  - Cluster mgmt   │  │  - Cluster mgmt   │             │
│  │  - Index creation │  │  - Index creation │             │
│  │  - Shard alloc   │  │  - Shard alloc    │             │
│  └──────────────────┘  └──────────────────┘             │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │  DATA NODE 3      │  │  DATA NODE 4      │             │
│  │                   │  │                   │             │
│  │  - Stores shards  │  │  - Stores shards  │             │
│  │  - CRUD operations│  │  - CRUD operations│             │
│  │  - Search execute │  │  - Search execute │             │
│  └──────────────────┘  └──────────────────┘             │
│                                                          │
│  ┌──────────────────┐                                   │
│  │  COORDINATING     │                                   │
│  │  NODE 5           │                                   │
│  │                   │  (routes requests, merges results)│
│  │  - No data        │                                   │
│  │  - No master role │                                   │
│  │  - Load balancer  │                                   │
│  └──────────────────┘                                   │
└──────────────────────────────────────────────────────────┘
```

### Node Types

| Node Type | Role | Hardware |
|-----------|------|----------|
| **Master-eligible** | Cluster management, metadata, shard allocation | CPU, less RAM/IO |
| **Data node** | Stores shards, executes searches | High RAM, fast SSD |
| **Coordinating node** | Routes requests, merges results, load balances | CPU, network |
| **Ingest node** | Pre-processes documents (enrich, transform) | CPU |
| **Machine Learning node** | Runs anomaly detection, ML models | High CPU, GPU |

### Index → Shards → Segments

```
INDEX: A logical collection of documents (like a database)
  │
  ├── Primary Shard 0 (on Node 3)
  │   ├── Segment 0 (immutable, sealed)
  │   ├── Segment 1 (immutable, sealed)
  │   └── Segment 2 (active, being written to)
  │
  ├── Replica Shard 0 (on Node 4) ← copy of Primary 0
  │
  ├── Primary Shard 1 (on Node 4)
  │   └── ...
  ├── Replica Shard 1 (on Node 3)
  │
  └── ...

  SHARD: A single Lucene index (independent search engine)
  → Each shard is a full, independent Lucene instance
  → Has its own inverted index, its own segments

  SEGMENT: An immutable, sealed portion of a shard's index
  → The inverted index is stored in segments
  → Segments are NEVER modified (immutable)
  → New writes create new segments
  → Old segments are merged periodically
```

### Why Shards?

```
1. HORIZONTAL SCALING:
   1 shard on 1 node → 1TB of data
   10 shards on 10 nodes → 10TB of data (linear scaling)

2. PARALLELISM:
   Query "running shoes" across 5 shards:
   → All 5 shards search simultaneously
   → Results merged and ranked
   → ~5x faster than single shard

3. HIGH AVAILABILITY:
   Primary shard 0 on Node A
   Replica shard 0 on Node B
   → If Node A dies → Node B still serves shard 0
```

### How Many Shards?

```
SHARDS = (expected_data_size / per_shard_size)

  Guidelines:
  - Each shard: 30-50GB optimal (maximum: 50GB)
  - Too few shards → can't scale (shards can't be split easily)
  - Too many shards → overhead (each shard = separate Lucene engine)

  Example:
    500GB of product data → 10-16 shards
    5TB of log data → 100-160 shards

  OVERSHARDING is the #1 Elasticsearch mistake.
  Each shard has memory and CPU overhead (~150MB minimum per shard).
  1,000 shards × 150MB = 150GB of overhead (wasted).
```

---

<a id="writes"></a>
## How Writes Work: Refresh, Flush, Merge

### The Write Path

```
Step 1: Document arrives at Coordinating Node

  Coordinating Node ──► "Index product_123: {name:'Nike shoes'}"

Step 2: Route to correct shard

  shard = hash(product_123) % num_primary_shards

  hash("product_123") % 5 = 2 → Shard 2

Step 3: Write to Primary Shard

  Shard 2 (Primary, on Node 3):

    1. Write to Translog (transaction log — for crash recovery)
    2. Write to Indexing Buffer (in-memory buffer)
    3. Return success to client

  At this point: Document is in memory but NOT yet searchable!

Step 4: Replicate to Replica Shard

  Shard 2 (Primary) ──► Shard 2 (Replica, on Node 4)
  Replica also writes to translog + indexing buffer

Step 5: REFRESH (default: every 1 second)

  Indexing Buffer
  ──────────────
  [doc 123, doc 456, doc 789]  (accumulated for 1 second)
       │
       ▼
  New Segment (written to disk, opened for searching)
  ─────────────────────
  ┌──────────────────────┐
  │ Segment_001.seg       │ ← Now searchable!
  │ doc 123: Nike shoes   │
  │ doc 456: Adidas socks │
  │ doc 789: Puma jacket  │
  └──────────────────────┘

  After refresh: Document is searchable (visible to queries)
  Before refresh: Document exists but is invisible to searches

Step 6: FLUSH (default: every 30 minutes or when translog > 512MB)

  1. All segments are fsync'd to disk (durable)
  2. Translog is cleared
  3. Data is now permanently on disk

  Refresh vs Flush:
    REFRESH → Makes recent documents searchable (in-memory → segment)
    FLUSH   → Makes data durable on disk (fsync + clear translog)
```

### Segment Merging

```
Over time, many small segments accumulate:

  Shard 2:
  ├── Segment 0:  [docs 1-100]      (1MB)
  ├── Segment 1:  [docs 101-200]    (1MB)
  ├── Segment 2:  [docs 201-300]    (1MB)
  ├── Segment 3:  [docs 301-400]    (1MB)
  ├── ...                            (1MB each)
  └── Segment 99: [docs 9901-10000] (1MB)

  100 segments × 1MB = 100MB
  → Each query must check ALL 100 segments
  → Inefficient (100 file reads)

MERGE PROCESS (happens in background):

  Merge small segments into bigger ones:
  ├── Segment A: [docs 1-1000]      (10MB) ← merged from 10 small ones
  ├── Segment B: [docs 1001-2000]   (10MB)
  └── ...

  10 segments × 10MB = 100MB (same data, fewer segments)
  → Each query checks only 10 segments
  → More efficient (10 file reads)

  Old small segments are deleted after merge completes.
  Merge is I/O and CPU intensive → throttle in production.
```

---

<a id="reads"></a>
## How Reads Work: Query Then Fetch

### Two-Phase Search

```
PHASE 1: QUERY (scatter)

  Coordinating Node
    │
    ├──► Shard 0 (Primary or Replica)  → Top 10 matching docs + scores
    ├──► Shard 1 (Primary or Replica)  → Top 10 matching docs + scores
    ├──► Shard 2 (Primary or Replica)  → Top 10 matching docs + scores
    ├──► Shard 3 (Primary or Replica)  → Top 10 matching docs + scores
    └──► Shard 4 (Primary or Replica)  → Top 10 matching docs + scores

  Each shard searches its LOCAL inverted index.
  Returns only doc IDs + scores (not full documents).
  This is a SCATTER operation.

PHASE 2: FETCH (gather)

  Coordinating Node
    │
    │  Merge all 5 × 10 = 50 results
    │  Sort by score
    │  Take top 10
    │  Request full documents for those 10
    │
    ├──► Shard 2: "Give me full doc for IDs [5, 12]"
    └──► Shard 3: "Give me full doc for IDs [8]"

  Returns full, ranked results to client.

  WHY TWO PHASES?
    → If shard returns 10 docs (not all matching), network transfer is small
    → Only the FINAL top 10 are fully fetched (saves disk I/O + network)
    → Each shard independently sorts by local score
```

### Routing

```
Without routing (default):
  GET /products/_search?q=shoes
  → Sent to ALL shards (scatter)
  → Every shard searched

With routing:
  PUT /products/_doc/123?routing=user_456
  → Document stored on shard = hash("user_456") % num_shards

  GET /products/_search?q=shoes&routing=user_456
  → Only searches the ONE shard where user_456's docs live
  → Much faster (1 shard vs all shards)

  Use case: Multi-tenant systems (route by tenant_id)
```

---

<a id="mapping"></a>
## Mapping and Dynamic Mapping

### What Is Mapping?

Mapping is like a database schema — it defines field types:

```json
// Create index with explicit mapping
PUT /products
{
  "mappings": {
    "properties": {
      "name": { "type": "text" },           // Full-text searchable
      "price": { "type": "float" },          // Numeric
      "in_stock": { "type": "boolean" },     // Boolean
      "tags": { "type": "keyword" },         // Exact match (not analyzed)
      "created_at": { "type": "date" },      // Date
      "description": { "type": "text", "analyzer": "english" }
    }
  }
}
```

### Text vs Keyword — Critical Distinction

```
TEXT:
  Analyzed → broken into tokens → added to inverted index
  Used for: Full-text search
  Query: "running shoes" matches "running" and "shoes"

  "Nike Air Running Shoes" → tokens: ["nike", "air", "run", "shoe"]
  → Searchable by individual words

KEYWORD:
  NOT analyzed → stored as-is (single token)
  Used for: Exact match, sorting, aggregations
  Query: "running shoes" matches ONLY the exact string "running shoes"

  "Nike Air Running Shoes" → token: ["Nike Air Running Shoes"]
  → Only exact match works

MULTI-FIELD (best of both):
  "name": {
    "type": "text",
    "fields": {
      "keyword": { "type": "keyword" }
    }
  }

  → name: Full-text search
  → name.keyword: Exact match + sorting + aggregation
```

### Dynamic Mapping (Convenient but Dangerous)

```
Dynamic mapping (default): Elasticsearch guesses field types on first insert.

  POST /products/_doc
  { "name": "Nike Shoes", "price": 99.99, "in_stock": true }

  → Elasticsearch creates mapping automatically:
    name: text (with .keyword sub-field)
    price: float
    in_stock: boolean

  Next day, someone accidentally sends:
  POST /products/_doc
  { "name": "Adidas", "price": "expensive" }  ← price is a string!

  → Elasticsearch tries to parse "expensive" as float → ERROR
  → Or worse: creates a separate field with different type

  DANGER: Once mapping is set, it CANNOT be changed.
  → Changing field type requires reindexing ALL data.
  → Use explicit mappings in production!
```

---

<a id="aggregations"></a>
## Aggregations — Analytics on Search Results

Aggregations are like SQL GROUP BY, but operating on search results.

### Bucket Aggregation (Group results)

```json
GET /products/_search
{
  "size": 0,  // Don't return any documents
  "aggs": {
    "by_category": {
      "terms": { "field": "category.keyword", "size": 10 }
    }
  }
}

// Result:
{
  "aggregations": {
    "by_category": {
      "buckets": [
        { "key": "Electronics", "doc_count": 15234 },
        { "key": "Clothing", "doc_count": 8901 },
        { "key": "Books", "doc_count": 5678 }
      ]
    }
  }
}
```

### Metric Aggregation (Calculate values)

```json
GET /products/_search
{
  "size": 0,
  "aggs": {
    "avg_price": { "avg": { "field": "price" } },
    "max_price": { "max": { "field": "price" } },
    "price_stats": { "stats": { "field": "price" } }
  }
}

// Result:
{
  "aggregations": {
    "avg_price": { "value": 1299.50 },
    "max_price": { "value": 99999.0 },
    "price_stats": {
      "count": 50000,
      "min": 10.0,
      "max": 99999.0,
      "avg": 1299.50,
      "sum": 64975000.0
    }
  }
}
```

### Nested Aggregations

```json
// Average price PER CATEGORY
GET /products/_search
{
  "size": 0,
  "aggs": {
    "categories": {
      "terms": { "field": "category.keyword" },
      "aggs": {
        "avg_price": { "avg": { "field": "price" } },
        "price_range": {
          "range": { "field": "price", "ranges": [
            { "to": 500 },
            { "from": 500, "to": 2000 },
            { "from": 2000 }
          ]}
        }
      }
    }
  }
}
```

---

<a id="tuning"></a>
## Performance Tuning

### 1. Use the Right Number of Shards

```
  Too few:  Can't distribute load across nodes
  Too many: Overhead kills performance

  Rule: shard_size = 30-50GB
    500GB data → 10-16 shards
    5TB data → 100-160 shards

  Rule: shards_per_node ≤ 20
    10 nodes × 20 shards/node = 200 shard max
```

### 2. Use Filter Context for Exact Matches

```json
// BAD: Query context (calculates score — unnecessary for filtering)
GET /products/_search
{
  "query": {
    "bool": {
      "must": [
        { "term": { "category": "electronics" } },
        { "term": { "brand": "apple" } }
      ]
    }
  }
}

// GOOD: Filter context (no scoring, cacheable)
GET /products/_search
{
  "query": {
    "bool": {
      "filter": [
        { "term": { "category": "electronics" } },
        { "term": { "brand": "apple" } }
      ]
    }
  }
}

// Filters:
//   - Skip scoring (faster)
//   - Cached automatically (repeated filters are instant)
//   - Combined with full-text search:
{
  "query": {
    "bool": {
      "must": [
        { "match": { "name": "iphone" } }    // Full-text (scored)
      ],
      "filter": [
        { "term": { "brand": "apple" } },    // Exact match (cached)
        { "range": { "price": { "lte": 1000 } } }
      ]
    }
  }
}
```

### 3. Use Keyword for Aggregations

```
// BAD: Aggregating on text field
GET /products/_search
{
  "aggs": { "categories": { "terms": { "field": "name" } } }  // text field!
}
→ ERROR: Text fields are not optimized for aggregations.
→ Must use fielddata=true (memory-intensive, slow)

// GOOD: Aggregating on keyword field
GET /products/_search
{
  "aggs": { "categories": { "terms": { "field": "name.keyword" } } }
}
→ Fast, efficient, correct.
```

### 4. Use Bulk API for Indexing

```json
POST /_bulk
{ "index": { "_index": "products", "_id": "1" } }
{ "name": "Product A", "price": 100 }
{ "index": { "_index": "products", "_id": "2" } }
{ "name": "Product B", "price": 200 }
{ "index": { "_index": "products", "_id": "3" } }
{ "name": "Product C", "price": 300 }

// One request for 3 documents (vs 3 separate requests)
// For 10,000 documents: 1 request vs 10,000 requests
```

### 5. Force Merge Read-Only Indices

```
// After indexing is complete (logs, historical data):
POST /logs-2024-01/_forcemerge?max_num_segments=1

// Merges all segments into one
// → Faster searches (1 segment to check vs many)
// → Less memory overhead
// → Only for read-only indices (force merge creates large segments)
```

---

<a id="pitfalls"></a>
## Common Pitfalls

### 1. Deep Pagination

```
// BAD: Page 1000 (getting results 10001-10010)
GET /products/_search
{
  "from": 10000,
  "size": 10
}

Why bad?
  → Each shard must find top 10010 results
  → Coordinating node merges 5 × 10010 = 50050 results
  → Sorts all 50050, returns last 10
  → Enormous memory + CPU waste for 10 results

  Default limit: 10,000 (from + size ≤ 10000)

// GOOD: Use search_after (cursor-based pagination)
GET /products/_search
{
  "size": 10,
  "sort": [{ "price": "asc" }, { "_id": "asc" }]
}
// Take the sort values of the last result:
GET /products/_search
{
  "size": 10,
  "sort": [{ "price": "asc" }, { "_id": "asc" }],
  "search_after": [99.99, "product_456"]  // Last result's sort values
}
// Each shard returns top 10 from this point. Efficient.
```

### 2. Mapping Explosion

```
Problem: Dynamic mapping creates a new field for every unique key.
  → User-generated keys (e.g., analytics events) → 100,000+ fields
  → Each field = separate inverted index → memory explosion

Fix:
  "index.mapping.total_fields.limit": 2000  // default 1000
  // Or disable dynamic mapping:
  "dynamic": false  // new fields ignored, not indexed
```

### 3. Using Text Fields for Sorting

```
// BAD: Sorting on text field
GET /products/_search { "sort": [{ "name": "asc" }] }
→ Error: text fields are analyzed (can't sort on tokenized data)

// GOOD: Sort on keyword sub-field
GET /products/_search { "sort": [{ "name.keyword": "asc" }] }
```

---

<a id="real-apps"></a>
## How Real Companies Use Elasticsearch

| Company | Use Case | Scale |
|---------|---------|-------|
| **Wikipedia** | Full-text search across all articles | 50+ million docs |
| **Flipkart** | Product search ("red running shoes under 2000") | Millions of products |
| **Uber** | Place/address search | Billions of locations |
| **Zomato** | Restaurant search ("pizza near me") | Millions of restaurants |
| **Netflix** | Movie/TV show search + log analytics | 260M users |
| **GitHub** | Code search across all repositories | 200M+ repositories |
| **Tinder** | Profile matching + search | 100M+ profiles |
| **LinkedIn** | People/job search | 700M+ members |
| **Swiggy** | Restaurant/dish search + geo queries | Millions of items |

### Flipkart Search Architecture (Example)

```
  User types "running shoes"
    │
    ▼
  Search Gateway (auto-complete, typo correction, query understanding)
    │
    ├──► Product Elasticsearch Cluster (full-text search)
    │     → Match products by name, description, tags
    │     → Score by relevance (BM25) × popularity × conversion rate
    │
    ├──► Catalog Service (inventory, price, availability)
    │     → Filter results by stock, price range, brand
    │
    ├──>> Ranking Service (ML-based re-ranking)
    │     → Re-rank by predicted click-through-rate
    │     → Personalize for user (browsing history)
    │
    └──>> Aggregation
          → Facets: category, brand, price range, size, color
          → Returns ranked, filtered, faceted results in ~50ms
```

---

<a id="build"></a>
## How YOU Can Build This

### Level 1: Docker Setup

```bash
docker run -d --name elasticsearch \
  -e "discovery.type=single-node" \
  -e "xpack.security.enabled=false" \
  -p 9200:9200 \
  docker.elastic.co/elasticsearch/elasticsearch:8.13.0

# Test it
curl http://localhost:9200
```

### Level 2: Create Index + Index Documents + Search

```bash
# Create index with mapping
curl -X PUT "localhost:9200/products" -H 'Content-Type: application/json' -d '{
  "mappings": {
    "properties": {
      "name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "price": { "type": "float" },
      "category": { "type": "keyword" },
      "tags": { "type": "keyword" },
      "description": { "type": "text", "analyzer": "english" }
    }
  }
}'

# Index documents
curl -X POST "localhost:9200/_bulk" -H 'Content-Type: application/json' -d '
{ "index": { "_index": "products", "_id": "1" } }
{ "name": "Nike Air Zoom Running Shoes", "price": 4999, "category": "footwear", "tags": ["running", "sports"], "description": "Professional running shoes with air cushioning" }
{ "index": { "_index": "products", "_id": "2" } }
{ "name": "Adidas Ultraboost Shoes", "price": 6999, "category": "footwear", "tags": ["running", "premium"], "description": "Premium running shoes with boost technology" }
{ "index": { "_index": "products", "_id": "3" } }
{ "name": "Puma Running Jacket", "price": 2999, "category": "clothing", "tags": ["running", "jacket"], "description": "Lightweight jacket for running" }
'

# Search (full-text)
curl "localhost:9200/products/_search?q=name:running+shoes&pretty"

# Search (structured query with filter)
curl -X GET "localhost:9200/products/_search" -H 'Content-Type: application/json' -d '{
  "query": {
    "bool": {
      "must": [{ "match": { "name": "running shoes" } }],
      "filter": [
        { "term": { "category": "footwear" } },
        { "range": { "price": { "lte": 6000 } } }
      ]
    }
  },
  "aggs": {
    "brands": { "terms": { "field": "tags.keyword" } },
    "avg_price": { "avg": { "field": "price" } }
  }
}'
```

### Level 3: Production Architecture

```
  Search Gateway
    │
    ▼
  Elasticsearch Cluster (3 nodes)
  ├── 3 Master-eligible nodes (cluster management)
  ├── 3 Data nodes (store + search) — 64GB RAM each
  └── 1 Coordinating node (load balancer)
    │
    ├── Index: products (5 shards × 1 replica = 10 shards total)
    ├── Index: categories (1 shard × 1 replica)
    └── Index: user_searches (for analytics)
    │
    ├── Bulk API for indexing (batch = 5MB)
    ├── Refresh interval: 30s (instead of default 1s) for high write rates
    └── Force merge read-only indices

  Monitoring:
  ├── Prometheus (metrics)
  ├── Kibana (visualization + cluster health)
  └── Curator (index lifecycle management — delete old indices)
```

---

## Common Interview Questions

**Q: How does Elasticsearch achieve fast full-text search?**

A: The inverted index. Instead of scanning documents one by one (full table scan in SQL), Elasticsearch maintains a term-to-document mapping. When you search "running shoes", it tokenizes the query to ["run", "shoe"], looks up both terms in the inverted index (O(1) hash lookup), intersects the document lists, and scores the results. This is why Elasticsearch searches billions of documents in ~10ms while SQL LIKE would take seconds.

**Q: Explain the difference between text and keyword types.**

A: Text fields are analyzed — broken into tokens, lowercased, stemmed — and stored in the inverted index for full-text search. You search for individual words. Keyword fields are stored as-is (single token), not analyzed. You search for exact values. Use text for search (product names, descriptions); use keyword for filtering, sorting, and aggregations (categories, brands, tags). Best practice: define fields as text with a keyword sub-field: `{ "name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } } }`.

**Q: What is a shard and how do you choose the number of shards?**

A: A shard is an independent Lucene index. Each shard has its own inverted index, stored on a single node. Sharding allows horizontal scaling (more shards → more nodes → more data) and parallelism (all shards are searched simultaneously). Choose shard count based on data volume: target 30-50GB per shard. 500GB data → 10-16 shards. Each additional shard adds ~150MB overhead, so oversharding is a real problem. Set shard count at index creation — changing it later requires reindexing.

**Q: What's the difference between refresh and flush?**

A: Refresh makes recent documents searchable. New documents go to an in-memory indexing buffer. Refresh (default every 1 second) writes this buffer as a new segment on disk and opens it for searching. Flush makes data durable. It fsyncs all segments to physical disk and clears the transaction log. Refresh = searchable. Flush = durable. Between flushes, data survives crashes via the transaction log (translog).

**Q: How does BM25 scoring work?**

A: BM25 scores documents based on term frequency (TF), inverse document frequency (IDF), and document length normalization. TF: More occurrences of the search term = higher score, but with saturation (10th occurrence matters less than 1st). IDF: Rare terms across all documents contribute more score than common terms. Length normalization: Shorter documents that match are scored higher than longer documents (terms are more concentrated). Parameters: k1 (default 1.2) controls TF saturation, b (default 0.75) controls length normalization.

**Q: Why shouldn't you use deep pagination (from + size)?**

A: When requesting page 1000 (from=10000), each shard must return its top 10010 results. The coordinating node merges 5 × 10010 = 50,050 results, sorts all of them, and returns only the last 10. This wastes enormous memory and CPU. Instead, use search_after (cursor-based): sort by a unique field, then pass the last result's sort values as the starting point for the next page. Each shard returns only 10 results from that point.

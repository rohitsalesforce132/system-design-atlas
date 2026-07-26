# 🏗️ System Design Atlas

## How the World's Biggest Apps Really Work — End to End

> **22 apps. 22 architectures. 12 component deep dives. 1 interview cheat sheet. Zero buzzwords.**
> Every component, every scale number, every trade-off — explained from scratch, with ASCII diagrams and "how you can build it too" sections.

---

## 📚 What's Inside

### Foundational Concepts (Read These First)

These building blocks apply to every system. Understanding them makes the app-specific deep dives 10x easier.

| # | Concept | What You Learn |
|---|---------|---------------|
| 1 | [Load Balancing](concepts/load-balancing.md) | Distributing traffic across servers — L4 vs L7, algorithms, health checks, global DNS |
| 2 | [Caching](concepts/caching.md) | Making reads 100x faster — Redis, patterns (cache-aside, write-through), eviction, common pitfalls |
| 3 | [Database Scaling](concepts/database-scaling.md) | Sharding, replication, partitioning, consistent hashing, SQL vs NoSQL at scale |
| 4 | [Message Queues](concepts/message-queues.md) | Async processing — Kafka, RabbitMQ, pub/sub, exactly-once, decoupling services |
| 5 | [CDN](concepts/cdn.md) | Global content delivery — edge caching, video streaming, adaptive bitrate, origin shields |
| 6 | [Microservices](concepts/microservices.md) | When/how to split a monolith — API gateways, service discovery, circuit breakers, sagas |
| 7 | [Consistency Models](concepts/consistency-models.md) | ACID vs BASE, CAP/PACELC theorems, strong vs eventual, tunable consistency, causal, session |

---

### 🔧 Components & Technologies Reference

Every database, protocol, messaging system, and infrastructure tool mentioned across all 22 deep dives — explained individually with alternatives, comparisons, and when to use each.

| Guide | What You Learn |
|-------|---------------|
| [Databases](components/databases.md) | Redis, MySQL, PostgreSQL, Cassandra, DynamoDB, Bigtable, Elasticsearch, MongoDB, S3, Spanner, ClickHouse, Snowflake, Neo4j, SQLite |
| [Messaging & Streaming](components/messaging.md) | Kafka, RabbitMQ, SQS, Redis Streams, NATS, Pulsar, Celery — queues vs logs, delivery guarantees, consumer groups |
| [Networking & Protocols](components/networking.md) | WebSocket, SSE, gRPC, WebRTC, MQTT, HTTP/2, QUIC, XMPP — handshakes, lifecycles, when to use |
| [API Architectures](components/api-architectures.md) | REST, GraphQL, gRPC, Protobuf, Long Polling — over/under-fetching, decision tree |
| [Infrastructure & DevOps](components/infrastructure-and-devops.md) | Nginx, HAProxy, Envoy, Zuul, CloudFront, Akamai, Cloudflare, Kubernetes, Spark, Flink, Prometheus, Grafana, Erlang, Go |

---

### App Deep Dives

Each deep dive covers **10 sections**: Scale Numbers → Architecture → Components → Data Model → Request Flow → Scaling Strategy → Tech Stack → Build Your Own → Design Decisions → Interview Questions.

| # | App | Category | Key Challenge |
|---|-----|----------|--------------|
| 1 | [WhatsApp](apps/whatsapp.md) | Messaging | Real-time message delivery at 2B+ user scale |
| 2 | [Facebook](apps/facebook.md) | Social Network | Social graph + News Feed for 3B users |
| 3 | [Instagram](apps/instagram.md) | Media / Social | Photo/video storage + feed generation |
| 4 | [TikTok](apps/tiktok.md) | Video / Social | Recommendation algorithm + video delivery |
| 5 | [YouTube](apps/youtube.md) | Video Platform | Video upload/transcode/stream at scale |
| 6 | [Netflix](apps/netflix.md) | Streaming | Global video delivery + recommendations |
| 7 | [Uber](apps/uber.md) | Ride-Sharing | Real-time matching + route optimization |
| 8 | [Amazon](apps/amazon.md) | E-Commerce | Product search + checkout + inventory |
| 9 | [Google Maps](apps/google-maps.md) | Navigation | Route calculation + real-time traffic |
| 10 | [Zoom](apps/zoom.md) | Video Conf | Real-time video for 100s of participants |
| 11 | [Twitter/X](apps/twitter.md) | Social | Timeline generation at massive scale |
| 12 | [Spotify](apps/spotify.md) | Music Streaming | Audio delivery + personalized recommendations |
| 13 | [Airbnb](apps/airbnb.md) | Marketplace | Search + booking + host management |
| 14 | [Google Search](apps/google-search.md) | Search Engine | Indexing the entire internet |

---

### 🏗️ Sample Architectures

**8 practical design blueprints** — how to architect common system types from scratch, with component selection rationale, schema design, API design, step-by-step request flows, and trade-off analysis.

| # | Architecture | Key Challenge |
|---|-------------|---------------|
| 1 | [E-Commerce Platform](architectures/ecommerce-platform.md) | Flash sale traffic, inventory consistency |
| 2 | [Real-Time Chat](architectures/realtime-chat.md) | WebSocket scaling, message delivery |
| 3 | [Video Streaming](architectures/video-streaming.md) | Upload → transcode → stream pipeline |
| 4 | [Notification System](architectures/notification-system.md) | Multi-channel fanout, delivery tracking |
| 5 | [URL Shortener](architectures/url-shortener.md) | 100:1 read:write ratio, short code generation |
| 6 | [Rate Limiter](architectures/rate-limiter.md) | Distributed coordination, 4 algorithms |
| 7 | [Event-Driven Microservices](architectures/event-driven-microservices.md) | Saga pattern, CQRS, event sourcing |
| 8 | [Multi-Tenant SaaS](architectures/multi-tenant-saas.md) | Tenant isolation, billing, data residency |

---

### 💻 Sample Project (Runnable)

**NebulaShop** — a mini social-commerce platform that ties together every component from the atlas in one Docker Compose stack. PostgreSQL, Redis, Elasticsearch, Kafka, WebSocket, MinIO, Nginx, Prometheus, and Grafana — all working together.

➡️ **[Sample Project →](sample-project/README.md)**

```bash
cd sample-project
docker-compose up -d
./scripts/init.sh
open http://localhost:8080
```

---

### 🎯 Interview Cheat Sheet

**30+ components, each with a 7-8 line definition packed with specific technical details that convince the interviewer you know it cold.** Internal mechanisms, numbers, trade-offs — exactly what you'd say when asked "Tell me about X."

➡️ **[Interview Cheat Sheet →](interview-cheatsheet.md)**

Covers: Redis, MySQL, PostgreSQL, Cassandra, DynamoDB, MongoDB, Elasticsearch, S3, Bigtable, Spanner, ClickHouse, Neo4j, Kafka, RabbitMQ, SQS, Redis Streams, WebSocket, SSE, gRPC, WebRTC, MQTT, HTTP/2, Nginx, HAProxy, Envoy, Kubernetes, Docker, CDN, Spark, Flink, Airflow, Prometheus, Grafana, Jaeger, Erlang, Go.

---

### 🇮🇳 Indian Tech Giants

Built for India-scale: billions of UPI transactions, Diwali sale flash traffic, IPL-level spikes, and hyperlocal delivery across 1000+ cities.

| # | App | Category | Key Challenge |
|---|-----|----------|--------------|
| 15 | [Flipkart](apps/flipkart.md) | E-Commerce | Big Billion Days flash sale at scale |
| 16 | [Zomato](apps/zomato.md) | Food Delivery | Hyperlocal restaurant discovery + delivery |
| 17 | [Swiggy](apps/swiggy.md) | Food Delivery | Instamart quick commerce + fleet tracking |
| 18 | [BigBasket](apps/bigbasket.md) | Grocery | Scheduled grocery delivery + supply chain |
| 19 | [Paytm](apps/paytm.md) | Fintech | Wallet + UPI + merchant payments |
| 20 | [PhonePe](apps/phonepe.md) | Fintech | UPI at billion-transaction scale |
| 21 | [Ola](apps/ola.md) | Ride-Sharing | Ride-hailing + Ola Play streaming |
| 22 | [Razorpay](apps/razorpay.md) | Fintech | Payment gateway + routing + settlement |

---

## 📖 How to Read This Repo

### If You're Learning System Design (Interview Prep)

1. Start with [concepts/](concepts/) — understand the building blocks
2. Pick apps by difficulty:
   - **Beginner:** WhatsApp, Twitter, Razorpay, URL Shortener
   - **Intermediate:** YouTube, Netflix, Uber, Amazon, Flipkart, Zomato
   - **Advanced:** Google Search, Google Maps, TikTok, PhonePe, Swiggy

### If You Want to Build Your Own Apps

Each app deep dive has a **"How YOU Can Build This"** section with:
- Simplified architecture for small scale
- Exact tech stack recommendations
- Step-by-step build order (what to build first)
- ASCII diagrams for the simplified version

---

## 🧭 The Scaling Ladder

```
Level 0: Single Server             ───  ~1K users
Level 1: App + DB Separation       ───  ~10K users
Level 2: Cache + CDN               ───  ~100K users
Level 3: Load Balancer + Replicas  ───  ~1M users
Level 4: Sharding + Microservices   ───  ~10M users
Level 5: Multi-Region + Global CDN ───  ~100M users
Level 6: Custom Infrastructure     ───  ~1B+ users
```

Every app in this repo operates at Level 5 or 6. The deep dives explain how they got there.

---

## 📊 Scale Comparison at a Glance

| App | Daily Active Users | Requests/sec | Data Stored | Primary DB |
|-----|-------------------|-------------|-------------|------------|
| WhatsApp | 2B+ | ~10M | 100+ PB | SQLite (device) + Erlang |
| Facebook | 2B+ | ~12M | 1000+ PB | MySQL (sharded) |
| YouTube | 122M+ | ~10M | EX+ (exabyte) | Bigtable + MySQL |
| Netflix | 260M+ | ~1M | 200+ PB | Cassandra + S3 |
| Uber | 25M+ | ~200K | 100+ PB | PostgreSQL + SchemaRDD |
| Amazon | 310M+ | ~10M | EX+ | DynamoDB + Aurora |
| Google Search | 5B+ | ~100K (queries/sec) | 100+ EB | Bigtable + custom |
| TikTok | 1B+ | ~10M | EX+ | Custom + RocksDB |
| Instagram | 2B+ | ~10M | 500+ PB | PostgreSQL + Cassandra |
| Twitter/X | 250M+ | ~300K | 500+ PB | MySQL (sharded) + Redis |
| Spotify | 600M+ | ~1M | 50+ PB | Cassandra + PostgreSQL |
| Airbnb | 150M+ | ~100K | 50+ PB | MySQL + DynamoDB |
| Zoom | 300M+ | N/A | 100+ PB | MySQL + Cassandra |
| Google Maps | 1B+ | ~1M | 50+ EB | Spanner + custom |
| **Flipkart** | **50M+** | **~500K (sale: 5M)** | **50+ PB** | **MySQL (sharded) + Cassandra** |
| **Zomato** | **20M+** | **~200K** | **10+ PB** | **PostgreSQL + Redis + Elasticsearch** |
| **Swiggy** | **15M+** | **~300K** | **20+ PB** | **PostgreSQL + Redis + DynamoDB** |
| **BigBasket** | **10M+** | **~50K** | **5+ PB** | **MySQL + MongoDB** |
| **Paytm** | **50M+** | **~500K** | **20+ PB** | **MySQL + Cassandra + Kafka** |
| **PhonePe** | **100M+** | **~10K TPS (UPI)** | **30+ PB** | **MySQL + Vitess + Kafka** |
| **Ola** | **20M+** | **~150K** | **15+ PB** | **PostgreSQL + Redis + EMQTT** |
| **Razorpay** | **10M+** | **~5K TPS** | **5+ PB** | **PostgreSQL + Redis + Kafka** |

---

## 🛠️ Tech Stack Cheat Sheet

### Databases
| Need | Use |
|------|-----|
| Transactions (money, orders) | PostgreSQL / MySQL |
| Massive scale + simple lookups | Cassandra / DynamoDB |
| Flexible schema | MongoDB |
| Full-text search | Elasticsearch |
| Social relationships | Neo4j (graph) |
| Real-time counters | Redis (sorted sets) |
| Time-series metrics | InfluxDB / TimescaleDB |
| Blob storage (video/images) | S3 / object storage |

### Messaging
| Need | Use |
|------|-----|
| Massive throughput (millions/sec) | Kafka |
| Flexible routing | RabbitMQ |
| Simple + already using Redis | Redis Streams |
| Fully managed | SQS / Pub/Sub |

### Caching
| Need | Use |
|------|-----|
| General purpose | Redis |
| Pure key-value, multi-threaded | Memcached |
| In-process (tiny app) | LRU Map |

---

## 🎯 Common Patterns Across All Apps

```
┌─────────────────────────────────────────────────────────────┐
│                     THE UNIVERSAL ARCHITECTURE                │
│                                                              │
│  Users                                                       │
│    │                                                         │
│    ▼                                                         │
│  CDN (static content: images, video, CSS, JS)               │
│    │                                                         │
│    ▼                                                         │
│  DNS (GeoDNS → nearest data center)                         │
│    │                                                         │
│    ▼                                                         │
│  Load Balancer (distribute traffic)                         │
│    │                                                         │
│    ▼                                                         │
│  API Gateway (auth, rate limit, routing)                    │
│    │                                                         │
│    ▼                                                         │
│  Microservices (User, Feed, Search, Notification...)        │
│    │                                                         │
│    ▼                                                         │
│  Cache Layer (Redis / Memcached)                             │
│    │                                                         │
│    ▼                                                         │
│  Database (sharded MySQL, Cassandra, etc.)                  │
│    │                                                         │
│    ▼                                                         │
│  Message Queue (Kafka for async processing)                 │
│    │                                                         │
│    ▼                                                         │
│  Workers (video transcoding, recommendation, analytics)     │
└─────────────────────────────────────────────────────────────┘
```

Every app in this repo is a variation of this pattern. The magic is in **what** they customize.

---

## 🤔 Why This Repo Exists

Most system design resources either:
1. **Too shallow:** "Use a cache and a load balancer." (But HOW? WHY? What kind?)
2. **Too academic:** Dense theory with no real-world application.
3. **Buzzword-heavy:** "Leverage cloud-native synergies." (What does that MEAN?)

This repo is none of those. It's:
- **Deep but accessible:** Every concept starts with an analogy.
- **Practical:** Every section ends with "how YOU can build this."
- **Real:** Actual scale numbers, actual tech stacks, actual trade-offs.

---

## 📝 License

MIT — free to use, share, and build upon.

---

> Built for deep understanding. Not for memorizing buzzwords.

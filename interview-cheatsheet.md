# System Design Interview Cheat Sheet

> **How to use this:** For every component, read the definition (7-8 lines), then scan **Where to Use**, **Advantages**, and **Use Cases**. That's exactly what you'd say in an interview when asked "Tell me about X and when would you use it?"

---

## 🗄️ Databases

### Redis
Redis is an in-memory key-value store that achieves sub-millisecond latency by keeping all data in RAM and using a single-threaded event loop — which eliminates lock contention and context switching entirely. It's not just key-value though: it supports rich data structures like sorted sets (powering leaderboards via skiplist + hash dual encoding), hashes (ziplist for small, hashtable for large), lists (quicklist = linked list of ziplists), and HyperLogLog for probabilistic counting in fixed 12KB. Persistence is optional via RDB (point-in-time snapshots through fork) or AOF (append-only command log with fsync everysec), and I typically enable both in production — RDB for backups, AOF for durability with max 1 second data loss. For scaling beyond one core, I'd use Redis Cluster which shards data across 16,384 hash slots using CRC16, with automatic MOVED redirections. The main pitfalls I watch for are: never run KEYS * in production (blocks the event loop — use SCAN instead), watch for big keys that block on deletion (use UNLINK), and handle cache stampedes with probabilistic early expiration or mutex locks.

**Where to Use:** Anywhere you need sub-millisecond reads: caching layer in front of PostgreSQL/MySQL, session storage, real-time leaderboards, rate limiting, pub/sub notifications, and distributed locks. If data fits in RAM and access pattern is key-based lookup, Redis is the default choice.

**Advantages:**
- 100,000+ operations per second per instance (in-memory, single-threaded = no lock overhead)
- Rich data structures (sorted sets, hashes, lists, HyperLogLog, geospatial) — not just key-value
- Atomic operations (INCR, ZADD, SINTER) — no race conditions
- Pub/Sub and Streams built in — no separate messaging system needed for simple cases
- Redis Cluster provides automatic sharding and failover across 16,384 hash slots

**Use Cases:**
- Cache user profiles, product data, API responses (cache-aside pattern with 5-min TTL)
- Session store (HSET with TTL — web apps, mobile app auth tokens)
- Real-time leaderboards (ZADD/ZREVRANGE — gaming,排行榜)
- Rate limiting (INCR + EXPIRE per user per minute window)
- Distributed locks (SET NX EX for resource locking across services)
- Pub/Sub for real-time notifications (WebSocket message bus between servers)

---

### MySQL (InnoDB)
MySQL with InnoDB uses a clustered index architecture — the table data IS the B+ tree, physically ordered by the primary key, which means range scans on PK are sequential disk reads and very fast. This is why auto-increment integer primary keys are ideal — they cause sequential appends, whereas UUID primary keys cause random inserts and page splits. Secondary indexes store the primary key value (not a row pointer), so a secondary index lookup requires two B+ tree traversals. MVCC is implemented via undo logs — old row versions are stored in undo logs so concurrent readers see a consistent snapshot without blocking writers. Durability is guaranteed by the binary log (binlog) for replication and the InnoDB redo log for crash recovery via write-ahead logging. At scale, companies like Facebook and Flipkart shard MySQL by user_id across thousands of instances, each holding ~1M users, with cross-shard joins done in application code. For managed sharding, Vitess (used by YouTube) sits between the app and MySQL, transparently routing queries to the correct shard.

**Where to Use:** Transactional systems requiring ACID guarantees — e-commerce orders, payments, user accounts, inventory. Any app with well-defined schema and complex relationships needing JOINs, foreign keys, and transactions.

**Advantages:**
- ACID compliance with battle-tested InnoDB engine (row-level locking, crash recovery)
- Clustered index makes PK lookups and range scans extremely fast
- Mature ecosystem — every tool, language, and cloud supports MySQL natively
- Read replication is simple and well-understood (master → replicas via binlog)
- Vitess enables horizontal sharding without application changes

**Use Cases:**
- E-commerce platform (orders, payments, inventory — Flipkart, Amazon)
- User management system (accounts, profiles, auth — Facebook runs thousands of shards)
- Content management (CMS with structured data and relationships)
- Financial applications requiring strict ACID (banking, ledger)
- SaaS multi-tenant applications (each tenant gets a shard)

---

### PostgreSQL
PostgreSQL uses a heap-based storage model — unlike MySQL's clustered index, the table data is unordered, and every index (B-tree, GIN, GiST, BRIN) points directly to rows via CTID (page number + slot offset), meaning any index is equally fast with a single lookup. MVCC stores multiple versions of each row (tuples) with xmin/xmax metadata — readers check these against their transaction snapshot, and dead tuples are cleaned up by autovacuum. Its biggest advantage is extensibility: PostGIS for geospatial queries, pgvector for AI/ML embeddings, TimescaleDB for time-series, and JSONB for indexed document queries — making it a true hybrid relational/document/geospatial/AI database. For connection management, I always put PgBouncer in front — it multiplexes 1,000 client connections down to 20 actual database connections.

**Where to Use:** When you need advanced queries (JSON, GIS, full-text search), geospatial applications, AI/ML vector search, or when you want one database that can handle relational + document + geo + time-series without separate systems. Also ideal for apps needing strict ACID with complex query patterns.

**Advantages:**
- Extensibility — PostGIS (geospatial), pgvector (AI embeddings), TimescaleDB (time-series), JSONB (documents)
- No clustered index means any index is equally fast (no secondary lookup penalty)
- MVCC means readers never block writers (high concurrency without locks)
- Advanced query capabilities (window functions, CTEs, materialized views, full-text search)
- Strong SQL compliance and custom data types/functions

**Use Cases:**
- Location-based apps (Zomato, Swiggy — PostGIS for "restaurants within 2km")
- AI/ML applications (pgvector for similarity search on embeddings)
- Financial systems requiring ACID (Razorpay — payment transactions)
- Apps needing both SQL and NoSQL (JSONB for flexible schema + relational joins)
- Analytics dashboards (materialized views + window functions for reporting)

---

### Cassandra
Cassandra is a wide-column NoSQL database designed for massive write throughput and zero downtime — it uses a peer-to-peer architecture with no master node, so any node can accept reads and writes with no failover time. Writes are extremely fast via LSM-tree: the write goes to a commit log (sequential append) and a memtable (in-memory), then acknowledges immediately — no random I/O, no locks. Reads check the memtable first, then use a bloom filter to skip SSTables that don't contain the key. Compaction merges SSTables in the background (Size-Tiered for write-heavy, Leveled for read-heavy, Time-Window for time-series). Consistency is tunable per query — ONE, QUORUM, LOCAL_QUORUM, ALL — and QUORUM reads + QUORUM writes guarantee strong consistency (R + W > RF by pigeonhole principle). The main anti-patterns: no secondary indexes (scatter-gather), no JOINs, and design the partition key around your query pattern.

**Where to Use:** Write-heavy workloads at massive scale (100K+ writes/sec), time-series data, IoT telemetry, always-on applications requiring 99.999% availability, multi-datacenter replication without downtime.

**Advantages:**
- 100,000+ writes/sec per node (sequential append, no locks, no read-before-write)
- Peer-to-peer architecture — no master, no single point of failure, no failover time
- Linear scalability — add nodes and get proportional capacity increase
- Multi-datacenter replication built in (cross-DC without external tools)
- Tunable consistency per query (ONE for speed, QUORUM for correctness, ALL for safety)

**Use Cases:**
- Time-series data: IoT sensor data, server metrics, activity logs (Netflix viewing history)
- Always-on applications: messaging, gaming (can't afford any downtime)
- Write-heavy logging: event tracking, user activity streams (Instagram, Spotify)
- Multi-region active-active: data replicated across continents with local writes
- Shopping cart / user session data at scale (high write volume, eventual consistency OK)

---

### DynamoDB
DynamoDB is AWS's fully managed NoSQL database that abstracts away all operational overhead — no servers, no sharding, automatic replication across 3 AZs, and single-digit millisecond latency at any scale. It hashes the partition key to distribute items across partition nodes, auto-splitting when a partition exceeds 10GB or 3,000 RCU/1,000 WCU. Each 4KB read costs 1 RCU (eventually consistent) or 2 RCU (strongly consistent), each 1KB write costs 1 WCU. For secondary access patterns, GSI (Global Secondary Index) allows querying by a different partition key. DynamoDB Streams provides change data capture for Lambda triggers. Global Tables enable multi-region active-active replication.

**Where to Use:** Serverless applications on AWS (Lambda + DynamoDB = zero infrastructure management), simple key-based lookups at massive scale, gaming state, IoT event ingestion, session management.

**Advantages:**
- Zero operations — AWS handles sharding, replication, backups, scaling automatically
- Single-digit millisecond latency at any scale (10 items or 10 trillion)
- Pay-per-use pricing (on-demand mode) — no capacity planning needed for variable workloads
- DynamoDB Streams → Lambda for event-driven architectures with zero infrastructure
- Global Tables for multi-region active-active with no application code changes

**Use Cases:**
- Serverless web apps (API Gateway → Lambda → DynamoDB — zero servers)
- Gaming leaderboards and player state (Supercell — Clash of Clans)
- Shopping cart (Amazon — the world's largest shopping cart runs on DynamoDB)
- Session management and user profiles at scale (Duolingo)
- IoT device state and telemetry ingestion (connected devices reporting state)

---

### MongoDB
MongoDB is a document database that stores data as BSON (Binary JSON) — each document can have a different structure, so I can add fields without migrations, ideal for rapid prototyping and CMS. It supports rich queries on nested fields, aggregation pipeline for multi-stage transformation, and built-in horizontal sharding where the mongos router distributes writes based on a shard key. Replication uses a replica set (one primary + secondaries) with automatic election. The trade-off is no JOINs — I denormalize by embedding or maintaining separate lookup collections. For schema design, embed when data is accessed together and bounded; reference when large or shared across many parents.

**Where to Use:** Rapid prototyping (schema evolves freely), content management systems, mobile app backends with offline sync, IoT data with flexible device schemas, catalog data with varying product attributes.

**Advantages:**
- Flexible schema — add/remove fields without migrations (ideal for agile development)
- Document model maps naturally to application objects (no ORM impedance mismatch)
- Aggregation pipeline for complex data transformations (multi-stage pipeline)
- Built-in sharding for horizontal scaling (mongos router distributes by shard key)
- Rich query language (nested field queries, array operators, geospatial, text search)

**Use Cases:**
- Content management (Adobe — articles, templates, media with varying structures)
- Product catalog with diverse attributes (eBay — each product has different fields)
- Mobile application backend with offline sync (MongoDB Realm)
- IoT device management (different sensors report different fields)
- User profiles and preferences (flexible schema handles evolving user attributes)

---

### Elasticsearch
Elasticsearch is a distributed full-text search engine built on Apache Lucene — instead of scanning every document (like SQL LIKE), it builds an inverted index mapping each term to documents containing it, enabling O(1) lookup. During indexing, text passes through analysis: character filters → tokenizer → token filters (lowercase, stop words, stemming, synonyms). So "Running Shoes" becomes ["run", "shoe"]. Relevance is scored using BM25 (saturation: 10th occurrence contributes less than 1st, normalizes for document length). Each index is split into shards, each shard is an independent Lucene instance with immutable segments. I differentiate text (analyzed, for search) from keyword (not analyzed, for filtering/sorting/aggregations). For filtering, I use the filter context (no scoring, cached, repeatable).

**Where to Use:** Any application where users search for text (product search, document search, log analytics), autocomplete/typeahead, "find nearby" geo queries, log aggregation and analytics (ELK stack).

**Advantages:**
- Inverted index enables instant full-text search (vs SQL LIKE full table scan)
- Fuzzy matching handles typos ("runing" matches "running")
- BM25 relevance scoring returns best matches first (not just any match)
- Aggregations provide real-time analytics on search results (facets, histograms)
- Scales horizontally (shards distributed across nodes, searched in parallel)

**Use Cases:**
- E-commerce product search (Flipkart — "red running shoes under 2000")
- Log and event analytics (ELK stack — Splunk alternative for log search)
- Document/content search (Wikipedia — full-text search across all articles)
- Auto-complete and typeahead (Google-style search suggestions)
- Geospatial search ("restaurants within 2km" — Zomato, Uber)

---

### Amazon S3
S3 is an object storage service with essentially infinite capacity and 99.999999999% (11 nines) durability — achieved by storing each object across multiple disks in 3 AZs (6+ copies), with continuous background scrubbing for bit rot, and checksums on every read. It's a flat key-value store: bucket + key uniquely identifies an object, no real folders (slashes are just prefixes). Storage classes range from Standard ($0.023/GB) to Glacier Deep Archive ($0.00099/GB, 12-hour retrieval). For large files, multipart upload splits into parallel parts. Presigned URLs enable client-direct uploads without sharing credentials. Since Dec 2020, S3 provides strong read-after-write consistency for all operations.

**Where to Use:** Storing any large unstructured data — user uploads (photos, videos, documents), application backups, data lake storage for analytics, static website hosting, log archive.

**Advantages:**
- Infinite capacity — no provisioning, no "disk full" errors, pay only for what you store
- 11 nines durability (losing 1 object per 10,000 years per 10M objects)
- Storage classes for cost optimization (auto-transition hot→cold with lifecycle policies)
- Presigned URLs enable direct client→S3 uploads (bypass server, save bandwidth)
- Event notifications trigger Lambda/SQS/SNS on object creation (event-driven processing)

**Use Cases:**
- Media storage (Netflix — movie files, Airbnb — property photos, Instagram — user images)
- Data lake (store Parquet/JSON for Athena/Presto/Spark analytics)
- Static website hosting (single-page apps, documentation sites)
- Backup and disaster recovery (database snapshots, cross-region replication)
- Application artifacts (Lambda deployment packages, Docker images via ECR)

---

### Google Bigtable
Bigtable is Google's petabyte-scale wide-column database designed for massive read/write throughput (millions of ops/sec) with single-digit millisecond latency. It uses a similar data model to Cassandra (row key, column families) but is fully managed on GCP with Google's Colossus filesystem. Data is split into tablets (contiguous row key ranges), each served by a tablet server. It's optimized for time-series, IoT, and as a backing store for Google products (Search, Maps, YouTube). The row key design is critical — data is sorted by row key and range scans are the primary access pattern.

**Where to Use:** Massive-scale time-series (IoT, monitoring), GCP-native applications needing millions of ops/sec, as a backing store for graph/search systems, financial market data.

**Advantages:**
- Petabyte scale with consistent single-digit ms latency (no performance degradation at scale)
- Fully managed on GCP (zero operations — Google handles scaling, replication, failures)
- Seamless integration with BigQuery, Dataflow, and other GCP services
- High write throughput optimized for time-series and event data
- Strong consistency per row (not tunable like Cassandra — always consistent)

**Use Cases:**
- Time-series at massive scale (IoT telemetry from millions of devices)
- Financial market data (real-time stock ticks, high-frequency trading data)
- Google product backends (Search index storage, Maps tile data, YouTube metadata)
- Real-time analytics backing store (pre-aggregated data for dashboards)
- Ad-tech (real-time bidding data, impression tracking)

---

### Google Spanner
Spanner is Google's globally distributed relational database providing ACID transactions across continents — using TrueTime (GPS receivers + atomic clocks in every data center with bounded ~7ms uncertainty) to assign globally meaningful commit timestamps and Paxos consensus for external consistency. It's a SQL database with auto-sharding (splits tables into splits), multi-region synchronous replication, and 99.999% availability. The trade-off is write latency (~100-200ms for global transactions) and cost.

**Where to Use:** When global ACID transactions are non-negotiable — financial systems spanning continents, global inventory, ad-tech real-time bidding, multi-region strongly-consistent catalogs.

**Advantages:**
- Global ACID transactions (the only database that does this at planet scale)
- External consistency (strongest isolation — stronger than serializable)
- 99.999% availability (survives entire region failures)
- SQL interface (familiar relational model, not NoSQL)
- Auto-sharding and auto-rebalancing (no manual partition management)

**Use Cases:**
- Global financial systems (cross-continent money transfer with ACID)
- Ad-tech real-time bidding (consistent inventory counts globally)
- Global inventory management (e-commerce with stock across regions)
- Multi-region SaaS (strongly consistent user data across all data centers)
- Gaming (global leaderboard with real-time consistent state)

---

### ClickHouse
ClickHouse is a columnar analytics database that's 100x faster than row-oriented databases for analytical queries — it stores data column-by-column, so querying "average age of 10M users" reads only the age column (1/Nth the I/O). It uses vectorized execution (batch processing in CPU-cache-friendly loops), aggressive compression (5-10x because similar values are adjacent), and can scan billions of rows in seconds. It's terrible for OLTP — point lookups and updates are expensive (requires rewriting partitions).

**Where to Use:** Real-time analytics dashboards, log/event analysis, user behavior analytics, ad-hoc querying over billions of rows, replacement for slow MySQL/PostgreSQL analytics queries.

**Advantages:**
- 100x faster analytics than row-oriented DBs (reads only needed columns)
- Billions of rows scanned in seconds (vectorized execution + compression)
- Real-time ingestion (stream data in continuously via Kafka)
- SQL interface (standard SQL — no new query language to learn)
- 5-10x better compression than row-oriented (similar values stored adjacent)

**Use Cases:**
- Real-time analytics dashboards (Uber — trip analytics, Cloudflare — DNS analytics)
- Log and event analysis (application logs, security events, audit trails)
- User behavior analytics (click streams, session analysis, funnel metrics)
- IoT/analytics (sensor data aggregation, real-time monitoring)
- Financial analytics (transaction analysis, risk metrics)

---

### Neo4j
Neo4j is a graph database storing data as nodes (entities) and edges (relationships) — optimized for traversing connections. A "friends-of-friends-of-friends" query requiring 6 JOINs in SQL is a single pattern match in Cypher. Internally uses an adjacency list — each node stores pointers to connected nodes, so traversing an edge is a pointer dereference (O(1)) rather than a B-tree lookup and join.

**Where to Use:** Social networks (friend graphs), recommendation engines, fraud detection (connection patterns), network topology, knowledge graphs, any application centered on relationship traversal.

**Advantages:**
- O(1) relationship traversal (pointer dereference vs SQL JOIN's B-tree lookups)
- Cypher query language: `MATCH (a)-[:FRIEND*3]->(friend)` — elegant graph queries
- No impedance mismatch — graph data model matches relationship-centric domains
- Index-free adjacency (don't need indexes to find connected nodes)
- ACID compliant (safe for transactional relationship data)

**Use Cases:**
- Social networks (friend recommendations, mutual connections — Facebook, LinkedIn)
- Fraud detection (find connection patterns between suspicious accounts)
- Recommendation engines (product recommendations based on purchase graph)
- Network/infrastructure topology (IT asset relationships, dependency mapping)
- Knowledge graphs (entity-relationship mapping, semantic search)

---

## 📨 Messaging & Streaming

### Apache Kafka
Kafka is a distributed append-only log — messages are persisted for days/weeks and can be re-read by multiple independent consumers, fundamentally different from RabbitMQ where messages are deleted after consumption. A topic is split into partitions (ordered sequences), and the partition is the unit of parallelism — hash(key) % num_partitions ensures all events for a user_id land on the same partition in order. Consumers organize into consumer groups where each partition is read by exactly one consumer. For durability: acks=all with min.insync.replicas=2 and RF=3. Kafka achieves millions/sec throughput through sequential disk I/O, page cache, zero-copy sends, and producer batching. For exactly-once: idempotent producer + transactional consumer.

**Where to Use:** Event streaming between microservices, real-time analytics pipelines, log aggregation, CDC (change data capture), any system needing event replay and multiple independent consumers.

**Advantages:**
- Event retention and replay (rewind offsets — unique among messaging systems)
- Multiple independent consumer groups (analytics + email + search all read same events)
- Millions of events/sec throughput (sequential I/O + page cache + zero-copy)
- Exactly-once semantics via transactions (idempotent producer + atomic commit)
- Decouples producers from consumers (add new consumers without touching producers)

**Use Cases:**
- Event-driven microservices (order event → payment + inventory + email + analytics)
- Real-time analytics pipeline (events → Spark/Flink → dashboard)
- Log aggregation (all services → Kafka → Elasticsearch/warehouse)
- CDC (database changes → Kafka → Elasticsearch index update, cache invalidation)
- Activity tracking (user clicks, page views → Kafka → analytics)
- LinkedIn (7T msgs/day), Netflix (700B/day), Uber (driver GPS stream)

---

### RabbitMQ
RabbitMQ is a traditional message broker built on AMQP — it excels at flexible message routing. The model is exchanges → queues: producers publish to an exchange, routing rules (bindings) determine which queue receives the message. Exchange types: direct (exact match), topic (pattern match "orders.*.created"), fanout (broadcast), headers (attribute match). Messages are deleted after consumer acknowledges — no replay. Supports priority queues, dead-letter exchanges, and TTL.

**Where to Use:** Task distribution (distribute work across N workers), request-reply RPC patterns, complex routing topologies, when messages should be consumed and deleted (not replayed).

**Advantages:**
- Flexible routing (direct, topic, fanout, header exchanges — powerful message routing)
- Message acknowledgment (consumer must confirm processing before deletion)
- Dead-letter queues (failed messages quarantined for inspection)
- Priority queues (high-priority messages processed first)
- Simpler than Kafka for straightforward task distribution

**Use Cases:**
- Task queue (background jobs: email sending, image processing, report generation)
- RPC over messaging (service A sends request, service B responds via reply queue)
- Fan-out workflow (order event → email + SMS + push notification + analytics)
- Work distribution (distribute 1000 tasks across 10 workers evenly)
- Dead-letter handling (failed messages don't block the queue)

---

### Amazon SQS
SQS is AWS's fully managed message queue — serverless, no brokers, infinite scaling, pay-per-request. Standard queues provide at-least-once delivery with no ordering. FIFO queues provide exactly-once processing and strict ordering (capped at 300 msgs/sec). The visibility timeout mechanism: when a consumer receives a message, it becomes invisible for N seconds; if not deleted within that window, it becomes visible again for retry. Dead-letter queues capture messages that fail after N attempts.

**Where to Use:** Decoupling services on AWS, background task processing, smoothing traffic spikes, when you need a queue without managing infrastructure.

**Advantages:**
- Fully managed — zero infrastructure, zero maintenance, auto-scales infinitely
- Pay-per-request (no idle costs — only pay for messages you send/receive)
- Visibility timeout prevents duplicate processing (message invisible during processing)
- Dead-letter queues automatically isolate poison messages
- Integrates natively with Lambda, SNS, EC2, ECS

**Use Cases:**
- Order processing pipeline (checkout → SQS → worker → ship)
- Background job queue (image processing, report generation)
- Traffic spike buffering (Big Billion Days — queue absorbs the spike)
- Decoupling microservices (producer doesn't need consumer to be available)
- Lambda trigger (SQS → Lambda for serverless task processing)

---

### Redis Streams
Redis Streams is Redis's built-in append-only log — like a mini-Kafka inside Redis. Supports consumer groups (workers dividing events), message IDs with timestamps (like Kafka offsets), pending entries list for unacked messages (XCLAIM for crash recovery), and blocking reads. The advantage over Kafka is simplicity and latency — it's already in your Redis instance, sub-millisecond, no separate cluster.

**Where to Use:** Lightweight event streaming within a single service, when you already have Redis and don't want Kafka's complexity, for small-to-medium event volumes (<100K/sec).

**Advantages:**
- Already in Redis — no new infrastructure to deploy or manage
- Sub-millisecond latency (in-memory operations)
- Consumer groups with pending entries list (crash recovery via XCLAIM)
- Simpler than Kafka (no ZooKeeper, no partitions to manage)
- Works with existing Redis monitoring and tooling

**Use Cases:**
- Lightweight event streaming (service internal events)
- Real-time notifications (user action → stream → notification service)
- Task queue with consumer groups (distributed work processing)
- Audit log (append-only event log within a service)
- IoT device event ingestion at small scale

---

## 🌐 Networking & Protocols

### WebSocket
WebSocket provides a persistent, full-duplex TCP connection — both sides can send messages at any time with ~2-14 bytes per frame overhead (vs ~800 bytes per HTTP request). It starts as HTTP with Upgrade header, server responds 101 Switching Protocols (computing Sec-WebSocket-Accept via SHA-1), then the connection switches to WebSocket protocol. Data is sent in frames with opcodes (text, binary, close, ping, pong). Client-to-server frames must be masked. I always implement heartbeats (ping/pong every 25-30 seconds) because NAT timeouts and mobile network switches silently kill connections. The hardest scaling challenge is connections are stateful — I use Redis Pub/Sub message bus to route messages between servers.

**Where to Use:** Real-time bidirectional communication — chat apps, multiplayer games, live collaboration, financial tickers, IoT device control.

**Advantages:**
- Full-duplex (both client and server push at any time — no polling)
- Near-zero latency after connection (~2-14 bytes per frame, not ~800 bytes per HTTP)
- Persistent connection (no TCP handshake per message)
- Binary and text frames (can send Protobuf, images, JSON)
- Works through firewalls (starts as HTTP on port 80/443)

**Use Cases:**
- Chat applications (WhatsApp, Slack, Discord — real-time messaging)
- Live collaboration (Google Docs — real-time editing)
- Multiplayer gaming (real-time game state sync)
- Financial dashboards (real-time stock prices, order book updates)
- Live order tracking (Zomato — rider location updates every 5 seconds)

---

### Server-Sent Events (SSE)
SSE is a one-way streaming protocol (server → client only) built on standard HTTP — the server keeps the response open and pushes events using text/event-stream content type, and the browser's EventSource API automatically reconnects on disconnect. Simpler than WebSocket — no protocol upgrade, no custom framing, works through all proxies.

**Where to Use:** Server-to-client push only (notifications, live scores, social feed updates) — when the client doesn't need to send data back over the same channel.

**Advantages:**
- Built on standard HTTP (no protocol upgrade — works through all proxies)
- Auto-reconnect built into EventSource API (no manual reconnection logic)
- Simpler than WebSocket (no custom framing, no ping/pong to implement)
- Uses HTTP/2 (multiplexed — many SSE streams on one connection)
- Browser-native API (EventSource — no libraries needed)

**Use Cases:**
- Live notifications (Twitter — real-time tweet notifications)
- Social feed updates (Facebook — new posts appear without refresh)
- Real-time score updates (sports scores, election results)
- Server status dashboards (live system metrics)
- Stock ticker display (server pushes prices, client just displays)

---

### gRPC
gRPC is Google's high-performance RPC framework that's 5-10x faster than REST+JSON — it uses Protocol Buffers (binary serialization with field numbers), HTTP/2 (multiplexing thousands of RPCs on one connection with HPACK header compression), and compiled type-safe stubs from .proto files. Four RPC types: unary, server streaming, client streaming, bidirectional streaming. Schema evolution: add fields freely, never change field numbers/types. Deadlines propagate through call chains. Load balancing requires client-side LB or Envoy (HTTP/2 long-lived connections defeat traditional L4 LBs).

**Where to Use:** Internal service-to-service communication in microservices, low-latency internal APIs, streaming data between services, when type safety and code generation matter.

**Advantages:**
- 5-10x faster than REST+JSON (Protobuf binary + HTTP/2 multiplexing + HPACK)
- Type-safe compiled stubs (compile-time errors, not runtime parsing failures)
- Four streaming modes (unary, server-stream, client-stream, bidirectional)
- Schema evolution (add fields without breaking existing clients)
- Deadline propagation (cancel entire call chain on timeout — prevent cascading failures)

**Use Cases:**
- Microservice communication (Google, Netflix, Uber — all internal RPC)
- Real-time data streaming (stock prices, IoT telemetry between services)
- Mobile backend API (Protobuf's compact size saves mobile bandwidth)
- Internal admin APIs (type-safe, fast, not exposed externally)
- Service mesh data plane (Envoy + gRPC = modern service communication)

---

### WebRTC
WebRTC is a peer-to-peer protocol for real-time audio/video/data in browsers — media flows directly between devices over UDP, bypassing servers. Connection setup: signaling (SDP exchange via WebSocket), ICE candidate gathering (STUN for public IP), NAT traversal (TURN relay if P2P fails). For >4 participants, SFU architecture: each participant sends one stream to server, server forwards appropriate streams. Simulcast: client sends multiple resolutions, SFU picks appropriate quality per receiver.

**Where to Use:** Video/audio calls, screen sharing, real-time gaming, peer-to-peer file transfer, any application needing real-time media between browsers.

**Advantages:**
- Peer-to-peer (media bypasses servers — lower latency, no server bandwidth cost)
- Built into browsers (no plugins, no downloads — WebRTC API is native)
- Adaptive bitrate (adjusts quality based on bandwidth — prevents buffering)
- Simulcast (multiple resolutions sent — server picks best for each receiver)
- Data channels (arbitrary binary data P2P — file sharing, gaming)

**Use Cases:**
- Video conferencing (Zoom, Google Meet, Microsoft Teams)
- Voice calls (Discord voice channels)
- Live streaming with ultra-low latency (<500ms vs 5-10s for HLS)
- Screen sharing (remote desktop, presentation tools)
- P2P file transfer (direct browser-to-browser, no server upload)

---

### MQTT
MQTT is a lightweight pub/sub protocol for IoT devices with constrained bandwidth — the header is just 2 bytes. Uses broker-based topic hierarchy ("home/livingroom/temperature"). Three QoS levels: 0 (at-most-once), 1 (at-least-once with ack), 2 (exactly-once). Retained messages keep last value for new subscribers. Last Will and Testament (LWT) auto-publishes if client disconnects unexpectedly.

**Where to Use:** IoT devices (sensors, smart home), mobile push notifications, low-bandwidth environments, Paytm soundbox-style connected devices, fleet tracking.

**Advantages:**
- 2-byte header (extremely lightweight — ideal for 2G/3G and constrained devices)
- Three QoS levels (choose reliability vs overhead per message)
- Retained messages (new subscribers instantly get current state)
- Last Will and Testament (automatic presence/offline detection)
- Topic hierarchy with wildcards (flexible subscription patterns)

**Use Cases:**
- IoT sensors (temperature, humidity, pressure — smart home, industrial)
- Paytm soundbox (payment confirmation via MQTT push to device)
- Fleet tracking (vehicle GPS reporting over MQTT)
- Smart home automation (light control, thermostat, security)
- Mobile push (Facebook Messenger originally used MQTT for push)

---

### HTTP/2
HTTP/2 is a binary protocol enabling multiplexing — thousands of concurrent requests on a single TCP connection, eliminating HTTP/1.1's one-request-per-connection bottleneck. Uses HPACK header compression (sends only deltas after first request). Stream priorities (client tells server what's important). Foundation for gRPC and modern web performance.

**Where to Use:** Any modern web application (HTTP/2 is supported by all modern browsers and servers), gRPC's transport layer, performance-critical APIs.

**Advantages:**
- Multiplexing (thousands of requests on one connection — no more 6-connection browser limit)
- HPACK header compression (repeated headers sent once, then deltas only)
- Binary framing (faster to parse than HTTP/1.1 text)
- Stream priorities (CSS loads before images — better page render)
- Server push (deprecated but historically sent resources proactively)

**Use Cases:**
- All modern web applications (browsers negotiate HTTP/2 automatically)
- gRPC transport (gRPC requires HTTP/2)
- Mobile apps (fewer connections = less battery drain)
- API backends serving many concurrent clients
- CDN edge-to-origin communication

---

## ⚖️ Infrastructure & DevOps

### Nginx
Nginx is a web server and reverse proxy using an event-driven, asynchronous architecture — a single worker per CPU core with epoll handles 10,000+ concurrent connections (vs Apache's thread-per-connection at ~2MB/thread). Load balancing with round-robin, least-connections, IP hash algorithms. Active health checks remove failed servers. SSL/TLS termination offloads CPU work. Also excellent as content cache (proxy_cache) and static file server (sendfile + tcp_nopush).

**Where to Use:** Reverse proxy in front of application servers, load balancer for web/API traffic, SSL termination, static file serving, API gateway (with Lua extensions).

**Advantages:**
- Event-driven (10,000+ concurrent connections per worker, ~1MB memory)
- Configurable load balancing (round-robin, least-connections, IP hash)
- SSL/TLS termination (offloads crypto from application servers)
- Static file serving with zero-copy (sendfile — kernel-level file → network)
- Caching (proxy_cache reduces backend load)

**Use Cases:**
- Reverse proxy + load balancer (in front of Node.js/Python/Go app servers)
- SSL termination (handle HTTPS at Nginx, backend speaks plain HTTP)
- Static file CDN origin (serve images, CSS, JS directly)
- API gateway (route /api/ → backend, /static/ → CDN, /admin/ → internal)
- Rate limiting (limit_req zone per IP or per user)

---

### Kubernetes
Kubernetes automates deployment, scaling, and management of containerized applications — describe desired state (3 replicas), K8s reconciles continuously. Control plane (API server, etcd, scheduler, controllers) manages; worker nodes run pods. A Pod is a group of containers sharing network/volumes. Deployments for stateless apps (rolling updates, rollbacks); StatefulSets for databases (stable identity, persistent volumes); DaemonSets for node-level daemons. Services (ClusterIP, NodePort, LoadBalancer, Ingress) provide networking. HPA auto-scales pod count; Cluster Autoscaler adds/removes nodes.

**Where to Use:** Any containerized application needing auto-scaling, self-healing, rolling updates, or multi-service orchestration. When you have more than a few containers to manage.

**Advantages:**
- Self-healing (pod dies → controller starts replacement in seconds)
- Horizontal auto-scaling (HPA adds pods based on CPU/memory — handles traffic spikes)
- Rolling updates and rollbacks (zero-downtime deploys, instant rollback on failure)
- Declarative configuration (YAML describes desired state — version controlled, reproducible)
- Service discovery and load balancing (DNS-based, automatic)

**Use Cases:**
- Microservices platform (manage 100+ services with independent deploy cycles)
- Auto-scaling web apps (handle Black Friday traffic — Shopify scales to 15K pods)
- ML training clusters (GPU scheduling — OpenAI)
- Batch processing (CronJobs for scheduled data pipelines)
- Database clusters (StatefulSets for PostgreSQL, Kafka, Elasticsearch)

---

### Docker
Docker packages applications with all dependencies into portable images — solving "works on my machine." Containers share the host OS kernel (unlike VMs with full OS each) using namespaces for isolation and cgroups for resource limits. Starts in milliseconds vs minutes for VMs. Images are layered (each Dockerfile instruction = one layer, cached for fast rebuilds).

**Where to Use:** Anywhere you package and deploy applications — microservices, CI/CD pipelines, local development environments, batch processing jobs.

**Advantages:**
- Consistent environments (same image runs on dev, staging, production)
- Lightweight (shares OS kernel — ~100MB vs ~2GB for a VM)
- Fast startup (milliseconds — just starting a process, not booting an OS)
- Layer caching (only rebuild changed layers — fast iterative development)
- Immutable infrastructure (image is versioned, reproducible, rollbackable)

**Use Cases:**
- Microservice packaging (each service = one Docker image, deploy anywhere)
- CI/CD pipelines (build image → test → push → deploy — consistent pipeline)
- Local development (docker-compose spins up DB + Redis + app with one command)
- Batch jobs (run containerized job, destroy when done)
- Multi-service local dev (replicate production environment locally)

---

### CDN (Cloudflare / CloudFront / Akamai)
A CDN places edge servers in hundreds of cities worldwide so users fetch content from a nearby server — reducing latency from cross-continent (~150ms) to local (~5ms). On cache miss, the edge fetches from origin, caches (TTL), and serves future requests from cache. Cloudflare (~330 cities, free tier, DDoS protection), CloudFront (~600 PoPs, AWS-integrated, Lambda@Edge), Akamai (~4,200 locations, enterprise SLAs).

**Where to Use:** Any application serving static content (images, CSS, JS, videos) to a global audience. Essential for video streaming, e-commerce image delivery, and any app with users in multiple regions.

**Advantages:**
- Reduces latency (content served from nearby edge — 5ms vs 150ms cross-continent)
- Reduces origin load (edge serves cached content — origin only handles misses)
- DDoS protection (edge absorbs attack traffic before reaching origin)
- Global reach (users everywhere get fast content)
- SSL/TLS termination at edge (offloads crypto from origin)

**Use Cases:**
- Static asset delivery (images, CSS, JS — every modern web app)
- Video streaming (Netflix Open Connect — cache appliances in ISP data centers)
- Software downloads (Ubuntu ISOs, npm packages, Docker images)
- Dynamic content acceleration (API responses cached for seconds)
- Edge computing (Cloudflare Workers — run code at edge for personalization)

---

## ⚙️ Data Processing

### Apache Spark
Spark is a distributed data processing engine up to 100x faster than Hadoop MapReduce — via in-memory computation (caches intermediate results in RAM). Uses RDDs (resilient distributed datasets) that track lineage for recovery. Transformations build a DAG, actions trigger execution. Catalyst optimizer rearranges the DAG. DataFrames/Datasets for structured data with SQL API.

**Where to Use:** Large-scale ETL (extract-transform-load), batch analytics on petabyte-scale data, machine learning model training (MLlib), interactive data science on big data.

**Advantages:**
- 100x faster than MapReduce (in-memory caching of intermediate results)
- Unified API (batch, streaming, ML, SQL — one engine, multiple workloads)
- Lazy evaluation (builds DAG, optimizes before executing — efficient)
- Fault tolerance (RDD lineage — lost partition recomputed automatically)
- Rich ecosystem (MLlib, GraphX, Structured Streaming)

**Use Cases:**
- ETL pipelines (extract from S3/DB → transform → load to warehouse)
- Machine learning (train models on TB-scale datasets with MLlib)
- Log analysis (process terabytes of application logs)
- Recommendation systems (collaborative filtering on large interaction matrices)
- Batch report generation (daily/weekly analytics dashboards)

---

### Apache Flink
Flink is a true stream processing engine (not micro-batch like Spark) — processes events one-at-a-time with sub-millisecond latency. Provides exactly-once via checkpointing (Chandy-Lamport snapshot). Supports event-time processing (handles out-of-order events via watermarks), stateful computations, and windowing (tumbling, sliding, session).

**Where to Use:** Real-time fraud detection, anomaly detection, live dashboards, real-time alerting, any use case requiring millisecond latency on streaming data with exactly-once guarantees.

**Advantages:**
- True streaming (one event at a time — sub-ms latency, not micro-batch)
- Exactly-once semantics (checkpointing with Chandy-Lamport algorithm)
- Event-time processing (handles out-of-order events correctly via watermarks)
- Stateful computation (maintains per-key state — running totals, sessions)
- Rich windowing (tumbling, sliding, session windows for time-based aggregation)

**Use Cases:**
- Real-time fraud detection (analyze transaction stream for suspicious patterns)
- Surge pricing (Uber — process driver location stream, compute demand/supply ratio)
- Real-time alerting (Netflix — anomaly detection on service metrics)
- Live dashboards (real-time business metrics, updated per event)
- IoT analytics (real-time sensor data processing with anomaly detection)

---

### Apache Airflow
Airflow is a workflow orchestration tool — define data pipelines as DAGs in Python, where nodes are tasks (SQL query, model training, email) and edges define dependencies. Scheduler triggers tasks based on schedule (cron) and dependencies. Workers execute across distributed nodes. Supports retries with backoff, SLA monitoring, XComs (pass data between tasks).

**Where to Use:** Orchestrating ETL pipelines, ML model retraining schedules, data quality checks, any multi-step data pipeline with dependencies and scheduling.

**Advantages:**
- Python-native (define pipelines as code — version controlled, testable, dynamic)
- Rich scheduling (cron expressions, dependencies, sensors for external triggers)
- Retry and SLA monitoring (automatic retries with backoff, alerts on late tasks)
- UI for monitoring (visual pipeline status, logs, manual trigger/rerun)
- Extensible (custom operators for any system — databases, APIs, cloud services)

**Use Cases:**
- ETL orchestration (extract API data → transform in Spark → load to warehouse)
- ML pipeline (daily model retrain → validate → deploy if better)
- Data quality checks (daily validation of warehouse tables)
- Report generation (weekly business reports — pull data, transform, email)
- Multi-source ingestion (pull from 10 APIs in parallel, merge, load)

---

## 📊 Monitoring & Observability

### Prometheus
Prometheus is a metrics collection and alerting system using a pull model — it scrapes metrics from instrumented services at regular intervals via HTTP /metrics. Metrics are multi-dimensional (name + labels: `http_requests_total{method="GET", status="200"}`). Stores in a time-series database optimized for range queries. Alerts written in PromQL. Designed for metrics (numeric time-series), not logs or traces.

**Where to Use:** Monitoring containerized applications (especially Kubernetes), infrastructure metrics (CPU, memory, disk, network), application metrics (request count, latency, error rate), alerting.

**Advantages:**
- Pull model (Prometheus scrapes — service doesn't need to know about Prometheus)
- Multi-dimensional labels (filter metrics by service, endpoint, status, etc.)
- PromQL (powerful query language for aggregations and rate calculations)
- Built-in alerting (Alertmanager routes alerts to Slack, PagerDuty, email)
- Service discovery (auto-discovers Kubernetes pods and services to scrape)

**Use Cases:**
- Kubernetes monitoring (default monitoring stack for K8s)
- Application metrics (request count, latency percentiles, error rates per endpoint)
- Infrastructure monitoring (CPU, memory, disk usage, network I/O)
- Alerting (CPU > 80% for 5 min → scale up; error rate > 1% → page on-call)
- SLO/SLA tracking (measure uptime and latency against service objectives)

---

### Grafana
Grafana is a visualization and dashboarding tool querying multiple data sources (Prometheus, Elasticsearch, InfluxDB, CloudWatch, PostgreSQL) to render time-series graphs, heatmaps, gauges, and tables. Supports templating variables (select service/environment/time range from dropdowns), thresholds (green/yellow/red), and alert rules notifying via Slack, PagerDuty, email.

**Where to Use:** Visualizing any time-series metrics — infrastructure dashboards, application performance monitoring, business metrics, real-time operations dashboards.

**Advantages:**
- Multi-source (query Prometheus + CloudWatch + PostgreSQL in one dashboard)
- Beautiful visualizations (graphs, heatmaps, gauges, tables, geo maps)
- Templating (dropdowns for environment/service/time range — one dashboard for all)
- Alerting (visual thresholds → Slack/PagerDuty/email notifications)
- Open source and widely adopted (standard in K8s monitoring stack)

**Use Cases:**
- Infrastructure dashboard (CPU, memory, network, disk across all servers)
- Application monitoring (request rate, latency p50/p95/p99, error rate)
- Business metrics (daily active users, revenue, conversion rate)
- Real-time operations dashboard (live traffic, queue depth, active connections)
- SLO dashboards (availability and latency vs SLO targets)

---

### Jaeger / Distributed Tracing
Jaeger is a distributed tracing tool — when a request traverses multiple microservices, a unique trace ID propagates via headers, and each service records spans (start time, end time, operation) for its portion. The result is a visual timeline: "Gateway: 5ms → Auth: 2ms → Order: 45ms → DB: 28ms." Essential for debugging latency in microservices.

**Where to Use:** Any microservice architecture with more than 2-3 services in a request path — essential for debugging where time is spent and identifying bottlenecks.

**Advantages:**
- End-to-end visibility (see exactly which service is slow in a request chain)
- Visual timeline (span breakdown shows time per service per operation)
- Overhead is low (OpenTelemetry SDK auto-instruments HTTP/gRPC/DB calls)
- Service dependency map (visualize which services call which)
- Latency optimization (find the slow service in a 10-service call chain)

**Use Cases:**
- Debugging slow requests in microservices (which of 10 services is the bottleneck?)
- Service dependency mapping (understand which services call which)
- Performance optimization (find N+1 query patterns, unnecessary downstream calls)
- Error tracing (trace a failed request across all services to find where it broke)
- Capacity planning (identify which services need more resources based on trace data)

---

## 🔤 Languages & Runtimes

### Erlang
Erlang was built by Ericsson for telecom switches — designed for massive concurrency (millions of lightweight processes at ~2KB each), fault tolerance ("let it crash" with supervisor restart), and hot code swapping (upgrade without downtime). WhatsApp chose Erlang because messaging maps to the actor model: each user connection is an independent process. WhatsApp ran 2B connections on ~1,000 servers.

**Where to Use:** Messaging/chat systems, telecom infrastructure, real-time systems requiring massive concurrency and fault tolerance, any system where "let it crash" supervision is appropriate.

**Advantages:**
- Millions of lightweight processes (~2KB each vs 2MB for OS threads)
- Built-in fault tolerance (supervisors restart crashed processes automatically)
- Hot code swapping (upgrade production systems without disconnecting users)
- Built-in distributed communication (nodes talk natively, no external messaging)
- Proven at telecom scale (Ericsson switches, WhatsApp 2B connections)

**Use Cases:**
- Messaging backends (WhatsApp — 2B connections on Erlang)
- Real-time communication servers (chat, presence, push)
- Telecom infrastructure (PBX, SMS gateways, call routing)
- Gaming servers (massive concurrent player connections)
- Financial trading systems (low-latency, fault-tolerant message processing)

---

### Go
Go (Golang) was created by Google for scalable, concurrent server software — goroutines are lightweight threads (~2KB stack) managed by the Go runtime, so I can spawn 100,000+ easily. Compiles to a single static binary (no JVM, no dependencies), making Docker images tiny and deployments clean.

**Where to Use:** High-performance microservices, CLI tools, network services, gRPC backends, any service where concurrency, fast compilation, and simple deployment matter.

**Advantages:**
- Goroutines (100,000+ lightweight concurrent threads — trivial parallelism)
- Single static binary (no JVM, no runtime dependencies — `scp binary && ./run`)
- Fast compilation (seconds, not minutes — fast developer iteration)
- Strong standard library (net/http, encoding/json, database/sql built in)
- Excellent tooling (go fmt, go test, pprof profiling, race detector)

**Use Cases:**
- Microservice backends (Discord, Uber, Twitch — high-performance APIs)
- CLI tools (kubectl, terraform, hugo — single binary, no dependencies)
- Network services (proxies, load balancers, API gateways)
- gRPC servers (Go has best-in-class gRPC support)
- Infrastructure tooling (Kubernetes itself is written in Go)

---

### Memcached
Memcached is a simpler alternative to Redis — pure, multi-threaded key-value cache with no data structures, no persistence, and no replication. Multi-threaded (uses all CPU cores). Slab allocator prevents fragmentation. True LRU eviction. Facebook runs thousands of Memcached instances.

**Where to Use:** Pure caching where you need multi-core utilization and absolute simplicity — no data structures, persistence, or pub/sub needed.

**Advantages:**
- Multi-threaded (utilizes all CPU cores — unlike Redis's single thread)
- Extremely simple (set, get, delete — that's it, no complexity)
- Low memory overhead (no data structure metadata, slab allocator is efficient)
- Battle-tested (Facebook, YouTube, Wikipedia — decades of production use)
- No single-thread bottleneck (higher throughput for pure key-value)

**Use Cases:**
- Database query cache (cache SQL results — Facebook's primary use)
- Session cache (store session data with TTL)
- Object cache (cache serialized objects — user profiles, product data)
- Rendered HTML cache (cache full page output for anonymous users)
- Rate limiting counters (INCR + EXPIRE per window)

---

### Protocol Buffers (Protobuf)
Protobuf is Google's binary serialization format that's 3-10x smaller and 10-100x faster than JSON — uses field numbers (1 byte) instead of field names, varint encoding (small numbers use fewer bytes), binary format (no braces/quotes/whitespace). Schema in .proto files, compiled to type-safe classes for any language. Schema evolution: add fields freely, never change field numbers/types.

**Where to Use:** Any high-performance serialization need — gRPC services, Kafka event schemas, internal data transfer, storage format for structured data, configuration files.

**Advantages:**
- 3-10x smaller than JSON (field numbers, varint, no delimiters)
- 10-100x faster serialization/deserialization (binary, no text parsing)
- Type-safe compiled stubs (compile-time errors, not runtime parsing failures)
- Schema evolution (add optional fields without breaking compatibility)
- Cross-language (generate Go, Python, Java, JS, C++ from one .proto file)

**Use Cases:**
- gRPC communication (wire format for all gRPC calls)
- Kafka event serialization (compact events with Schema Registry validation)
- Data storage format (store structured data compactly on disk)
- Mobile API responses (save bandwidth on cellular networks)
- Inter-service data exchange (typed contracts between microservices)

---

### GraphQL
GraphQL solves REST's over-fetching (downloading 50 fields when you need 3) and under-fetching (5 API calls to render one screen) — client specifies exactly which fields it wants in a single query. Strongly-typed schema with resolver functions per field. Mutations for writes, subscriptions for real-time over WebSocket.

**Where to Use:** Mobile apps (save bandwidth by fetching only needed fields), multiple clients with different data needs (web vs mobile vs TV), rapid frontend iteration (add fields without new API endpoints).

**Advantages:**
- No over-fetching (client requests exactly the fields it needs — saves bandwidth)
- No under-fetching (nested data in one request — no multiple round trips)
- Single endpoint (one /graphql endpoint handles all queries — simpler client)
- Type-safe schema (strongly typed contract — autocomplete, validation)
- Real-time via subscriptions (WebSocket-based live updates)

**Use Cases:**
- Mobile apps (Instagram, Facebook — minimize data transfer on cellular)
- Multi-platform clients (different data needs for web, iOS, Android, TV)
- Rapid frontend development (frontend changes without new API endpoints)
- API aggregation layer (GraphQL gateway federating multiple microservices)
- Real-time collaboration (subscriptions for live document editing)

---

### ZooKeeper
ZooKeeper is a distributed coordination service providing configuration management, distributed locking, leader election, and service registry — a replicated hierarchical key-value store with ZAB (Paxos variant) for strong consistency. Ensemble of 3-5 nodes provides quorum. Kafka traditionally used ZooKeeper for metadata and leader election (being replaced by KRaft).

**Where to Use:** Distributed systems needing coordination primitives — leader election, distributed locks, configuration management, service registry. When building a distributed system that needs agreement.

**Advantages:**
- Strong consistency (ZAB protocol — all writes go through leader, ordered)
- Watches/notifications (clients get notified when data changes)
- Hierarchical namespace (filesystem-like organization for configuration)
- Battle-tested (Kafka, HBase, Solr all depend on ZooKeeper)
- ephemeral nodes (auto-deleted when client disconnects — perfect for presence/locks)

**Use Cases:**
- Leader election (Kafka partition leaders, HBase master election)
- Distributed locking (coordinate access to shared resources across services)
- Configuration management (store and broadcast cluster configuration)
- Service registry (services register → clients discover them)
- Cluster membership (track which nodes are alive in a distributed system)

---

### etcd
etcd is a distributed key-value store — the backbone of Kubernetes, storing all cluster state. Uses Raft consensus for strong consistency: writes go through leader, replicated to quorum, committed after majority acknowledgment. Cluster of 3-5 nodes provides fault tolerance. Flat key-value model with lease-based TTLs. Linearizable reads by default.

**Where to Use:** Service discovery, distributed configuration, leader election, Kubernetes cluster state, any distributed system needing a strongly-consistent metadata store.

**Advantages:**
- Strong consistency via Raft (linearizable reads and writes)
- Lease-based TTL (keys auto-expire — perfect for service registration/locks)
- Watch API (get notified on key changes — configuration updates)
- Lightweight (single binary, simple deployment — lighter than ZooKeeper)
- Kubernetes native (the standard metadata store for K8s clusters)

**Use Cases:**
- Kubernetes cluster state (stores pods, services, deployments — the source of truth)
- Service discovery (services register with TTL lease, clients watch for changes)
- Distributed configuration (store and broadcast config changes across services)
- Leader election (multiple instances use etcd to elect a leader)
- Distributed locking (compare-and-swap operations for atomic locks)

---

### Vitess
Vitess is a database clustering layer for MySQL making it horizontally scalable — the app thinks it's one MySQL instance, but Vitess transparently routes queries to the correct shard. VTGate (proxy) parses SQL and routes via vindex (shard key). VTTablet (sidecar) manages each MySQL instance. Handles cross-shard transactions, online schema migrations, and shard splits.

**Where to Use:** When MySQL is hitting vertical scaling limits and you need horizontal sharding without rewriting the application. When you have an existing MySQL app that needs to scale beyond one machine.

**Advantages:**
- Transparent sharding (application code doesn't change — Vitess handles routing)
- Online schema migrations (ALTER TABLE without locking — zero downtime)
- Connection pooling (reduces MySQL connection overhead)
- Cross-shard transactions (two-phase commit for scattered writes)
- Battle-tested at YouTube scale (thousands of shards)

**Use Cases:**
- Scaling MySQL beyond one machine (YouTube, Slack, Square)
- Sharding an existing MySQL application without rewriting it
- Multi-tenant SaaS (each tenant gets their own shard transparently)
- High-traffic web apps (news, social media with millions of users)
- Database migration (split shards as data grows — online resharding)

---

### PgBouncer
PgBouncer is a lightweight connection pooler for PostgreSQL — maintains a small pool of database connections (e.g., 20) while accepting thousands of client connections. Critical because each PostgreSQL connection uses ~10MB of RAM. Transaction pooling mode is most efficient (connection per transaction).

**Where to Use:** Always put PgBouncer in front of PostgreSQL in production — it's the single highest-impact performance optimization for connection-heavy applications.

**Advantages:**
- Dramatically reduces PostgreSQL memory usage (20 connections vs 1,000)
- Transaction pooling (most efficient — connection returned after each transaction)
- Lightweight (single process, ~2MB memory footprint)
- Zero code changes (application connects to PgBouncer as if it's PostgreSQL)
- Connection queuing (excess connections queue instead of failing)

**Use Cases:**
- Web applications with many concurrent connections (1000+ clients → 20 DB connections)
- Serverless applications (Lambda functions connecting to PostgreSQL)
- Microservices (each service opens connections — PgBouncer multiplexes them)
- Connection spike protection (sudden traffic surge doesn't crash PostgreSQL)
- Multi-tenant SaaS (many tenants, limited database connections)

---

### Istio
Istio is a service mesh built on Envoy proxies — provides traffic management, security, and observability for microservices without changing application code. Every pod gets a sidecar Envoy proxy intercepting all traffic. Enables mTLS (automatic encryption), intelligent routing (canary, circuit breaking), and distributed tracing.

**Where to Use:** Large microservice deployments (>50 services) where mTLS, fine-grained routing, and observability are worth the operational complexity.

**Advantages:**
- Zero-code mTLS (automatic encryption between all services — no app changes)
- Fine-grained traffic control (canary deploys, traffic mirroring, circuit breaking)
- Automatic distributed tracing (Envoy injects trace headers)
- Policy enforcement (rate limiting, access control — at the proxy level)
- Multi-platform (works on Kubernetes, VMs, hybrid environments)

**Use Cases:**
- Canary deployments (5% traffic to new version, monitor, increase gradually)
- mTLS between all microservices (security/compliance requirement)
- Circuit breaking (stop cascading failures — stop sending to failing services)
- Traffic mirroring (shadow traffic to new version for testing without impact)
- A/B testing (route percentage of traffic to different versions)

---

### Apache Pulsar
Pulsar separates compute (brokers) from storage (BookKeeper) — brokers are stateless, enabling independent scaling of throughput and storage. Supports multi-tenancy natively, geo-replication built-in, and tiered storage (old segments auto-move to S3).

**Where to Use:** When you need multi-tenancy, compute-storage separation, or native geo-replication — and Kafka's operational model doesn't fit.

**Advantages:**
- Compute-storage separation (scale brokers and storage independently)
- Native multi-tenancy (multiple teams share one cluster with isolated namespaces)
- Geo-replication built-in (cross-region replication without external tools)
- Tiered storage (old data auto-moves to S3 — infinite retention at low cost)
- Both queuing and streaming (shared, exclusive, failover subscriptions)

**Use Cases:**
- Multi-tenant streaming platform (one Pulsar cluster for all teams)
- Geo-replicated event streaming (active-active across continents)
- Long-term event retention (tiered storage moves old events to S3 cheaply)
- Mixed workload (queue for task distribution + log for event streaming)
- Yahoo, Twitter, Tencent — large-scale messaging with multi-tenancy

---

### Amazon Kinesis
Kinesis is AWS's managed Kafka alternative — serverless, auto-scales based on shard count. Each shard handles 1MB/sec input and 2MB/sec output. Records ordered within shard, retained 24h-365 days. Producers use KPL, consumers use KCL for shard assignment and checkpointing.

**Where to Use:** AWS-native applications wanting managed streaming without operating Kafka. When you're already on AWS and want zero-ops streaming.

**Advantages:**
- Fully managed (no brokers to provision, patch, or monitor)
- Auto-scaling (add/remove shards based on throughput)
- Integrates with AWS ecosystem (Lambda, Firehose, Glue, SageMaker)
- Pay-per-shard (predictable pricing for steady workloads)
- KCL handles shard assignment and checkpointing automatically

**Use Cases:**
- IoT data ingestion (sensor data → Kinesis → analytics)
- Application log streaming (all services → Kinesis → S3/Elasticsearch)
- Real-time analytics (click stream → Kinesis → Lambda → dashboard)
- Data pipeline (Kinesis → Firehose → S3 → Athena/Redshift)
- Live video stream ingestion (Kinesis Video Streams for camera feeds)

---

### Amazon CloudFront
CloudFront is AWS's CDN with ~600 edge locations — caches static content, routes dynamic requests to origin. Integrates with S3 (origin), Lambda@Edge (run code at edge), and WAF (security). TTL-based caching with invalidation. Pricing based on data transfer out and request count.

**Where to Use:** AWS-native applications needing CDN delivery. When you need edge compute (Lambda@Edge for auth, A/B testing, personalization at edge).

**Advantages:**
- Native AWS integration (S3, ALB, Lambda@Edge, WAF — all work together seamlessly)
- Lambda@Edge (run JavaScript at edge locations — auth, A/B test, personalize without origin)
- Origin Access Identity (secure S3 content — only accessible via CloudFront)
- AWS WAF integration (SQL injection / XSS protection at edge)
- Pay-per-use (no upfront cost — pay for data transferred)

**Use Cases:**
- Static asset delivery for S3-hosted websites (images, CSS, JS)
- Video streaming (HLS/DASH segments cached at edge)
- Lambda@Edge personalization (show different content by geography)
- API acceleration (cache API responses at edge for seconds)
- Secure content delivery (WAF + CloudFront = DDoS protection + CDN)

---

### Akamai
Akamai is the oldest and largest CDN (~4,200 edge locations in 130+ countries) — enterprise-grade with SLA guarantees, dedicated support, and advanced security (Kona Site Defender). Apple, Microsoft, and Fortune 500 use Akamai.

**Where to Use:** Enterprise applications requiring the largest physical footprint, dedicated SLAs, or specific compliance certifications. When cost is secondary to reach and support.

**Advantages:**
- Largest physical footprint (~4,200 locations — more edge nodes than anyone)
- Enterprise SLAs (guaranteed uptime with financial penalties)
- Advanced security (Kona Site Defender — enterprise-grade DDoS protection)
- Dedicated support (named technical account manager)
- Edge compute (EdgeWorkers — run JavaScript at Akamai edge)

**Use Cases:**
- Enterprise CDN (Apple, Microsoft — global content delivery at massive scale)
- DDoS protection (financial, healthcare, government — Prolexic routed through Akamai)
- Video streaming for broadcasters (Olympics, World Cup, Netflix competitors)
- Dynamic site acceleration (speed up dynamic content with route optimization)
- Enterprise security (WAF, bot management, API security at edge)

---

### Amazon Aurora
Aurora is AWS's cloud-native MySQL/PostgreSQL — separates compute from storage (distributed volume replicated across 3 AZs). Storage auto-scales to 128TB. Up to 15 read replicas with sub-10ms replication lag. Failover in ~10 seconds (no data copying). Aurora Serverless v2 auto-scales compute.

**Where to Use:** Managed MySQL/PostgreSQL on AWS when you need fast failover, many read replicas, auto-scaling storage, and don't mind paying 20% more than RDS.

**Advantages:**
- Compute-storage separation (fast failover — no data copying, ~10s promotion)
- Up to 15 read replicas with sub-10ms lag (vs MySQL's 100ms+ async)
- Auto-scaling storage (up to 128TB — no provisioning)
- Aurora Serverless v2 (auto-scale compute from 0.5 to 128 ACU)
- MySQL/PostgreSQL compatible (no application changes needed)

**Use Cases:**
- High-availability MySQL/PostgreSQL (fast failover for mission-critical apps)
- Read-heavy applications (15 replicas handle massive read traffic)
- Variable workloads (Aurora Serverless auto-scales for spiky traffic)
- Multi-AZ disaster recovery (storage replicated across 3 AZs)
- SaaS platforms (multi-tenant databases with per-tenant isolation)

---

### Apache Hadoop / HDFS
Hadoop is a distributed storage (HDFS) and processing (MapReduce) framework. HDFS splits files into 128MB blocks across DataNodes with 3x replication. MapReduce sends compute to data (data locality). NameNode tracks block locations. Pioneered "move compute to data" — today largely superseded by Spark and S3.

**Where to Use:** Existing Hadoop ecosystem investments, on-premise data lakes, batch processing at petabyte scale where cloud (S3+Spark) isn't an option.

**Advantages:**
- Data locality (compute runs on nodes that have the data — minimizes network transfer)
- Petabyte-scale storage (HDFS distributes across hundreds of nodes)
- Fault tolerance (3x replication — data survives node failures)
- Ecosystem (Hive, Pig, HBase, Sqoop — mature tool ecosystem)
- Cost-effective on-premise (commodity hardware, no cloud lock-in)

**Use Cases:**
- On-premise data lake (store and process petabytes of data without cloud)
- Batch ETL (MapReduce for extract-transform-load at massive scale)
- Data warehousing (Hive — SQL queries on HDFS data)
- Log processing (aggregate and analyze terabytes of server logs)
- Archival storage (HDFS as a long-term data store with replication)

---

### Presto / Trino
Presto/Trino is a distributed SQL query engine for ad-hoc analytics across multiple data sources — queries S3, HDFS, MySQL, Kafka simultaneously without moving data. Compute-only engine (no storage). Coordinator parses SQL, workers scan data in parallel. Perfect for interactive analytics on data lakes.

**Where to Use:** Running SQL queries across multiple data sources without ETL — analytics on S3 data lakes, federated queries across MySQL + PostgreSQL + Kafka, interactive dashboards on big data.

**Advantages:**
- Federated queries (query S3 + MySQL + Kafka in one SQL statement)
- No data movement (pushes queries to where data lives — no ETL needed)
- Fast interactive queries (sub-second to seconds for ad-hoc analytics)
- Standard SQL (ANSI SQL — no new query language)
- Compute-only (no storage to manage — just point at existing data)

**Use Cases:**
- Data lake analytics (query Parquet/ORC files on S3 with SQL)
- Federated queries (join MySQL user data with Kafka event data)
- Interactive dashboards (real-time querying without warehouse loading delay)
- Ad-hoc data exploration (data scientists exploring large datasets with SQL)
- Replacing warehouse ETL (skip "load to warehouse" — query data where it lives)

---

### Snowflake
Snowflake is a cloud-native data warehouse with separation of compute and storage — storage on S3/Azure/GCS, compute via independent virtual warehouses (start/stop/scale independently). Supports SQL, semi-structured data (JSON in VARIANT columns), and data sharing.

**Where to Use:** Fully managed data warehousing with zero infrastructure management — when you want SQL analytics without managing servers.

**Advantages:**
- Compute-storage separation (scale compute up/down without affecting storage)
- Multi-cloud (runs on AWS, Azure, GCP — no cloud lock-in)
- Data sharing (share datasets with external companies without copying data)
- Semi-structured support (JSON, Avro, Parquet in VARIANT columns — no separate NoSQL)
- Zero maintenance (no indexing, no tuning, no vacuuming — Snowflake handles everything)

**Use Cases:**
- Cloud data warehouse (replace on-premise Oracle/Teradata)
- Analytics for SaaS companies (run all business analytics in SQL)
- Data sharing (share live data with partners/customers without ETL)
- JSON analytics (query nested JSON with SQL — no separate document store)
- Ad-hoc analytics (data scientists run queries without DBA involvement)

---

### Redshift / BigQuery
Redshift is AWS's managed columnar data warehouse (MPP — leader node + compute nodes). BigQuery is Google's serverless alternative (pay-per-query, no clusters). Both excel at analytical queries on TB-PB scale data using columnar storage.

**Where to Use:** Cloud data warehousing — Redshift for AWS-native with predictable workloads, BigQuery for serverless with unpredictable/spiky workloads.

**Advantages:**
- Columnar storage (reads only needed columns — fast analytical queries)
- MPP architecture (parallel query execution across many nodes)
- Managed (no hardware, no setup — just load data and query)
- Pay-per-use (BigQuery: $5/TB scanned — no idle costs; Redshift: per-node hourly)
- SQL interface (standard SQL — no new skills needed)

**Use Cases:**
- Cloud data warehouse (central analytics store for BI and reporting)
- Marketing analytics (customer segmentation, campaign analysis)
- Financial reporting (quarterly results, revenue analysis)
- Product analytics (user behavior, feature adoption metrics)
- Machine learning data preparation (aggregate features for ML models)

---

### Schema Registry
Schema Registry stores and validates Protobuf/Avro schemas for Kafka events — producers register schema, consumers fetch it, registry enforces compatibility (BACKWARD, FORWARD, FULL). Prevents the #1 event-driven architecture bug: producer changes format, all consumers crash.

**Where to Use:** Any Kafka deployment using Avro or Protobuf — essential for multi-team event-driven architectures where producers and consumers are owned by different teams.

**Advantages:**
- Prevents breaking changes (rejects incompatible schema changes before production)
- Centralized schema management (one source of truth for event formats)
- Versioning (track schema evolution over time — v1, v2, v3)
- Self-documenting (consumers discover the schema from the registry — no coordination needed)
- REST API (register, fetch, check compatibility via HTTP)

**Use Cases:**
- Kafka event schemas (Netflix, LinkedIn — all events have Avro schemas registered)
- Multi-team event contracts (Team A produces, Team B consumes — registry enforces contract)
- Schema evolution (add fields safely with backward-compatible defaults)
- Data governance (audit what schema was used for each event version)
- Consumer code generation (generate Python/Java classes from registered schemas)

---

### Apache Avro
Avro is a data serialization system used heavily with Kafka — JSON for schema, binary for data. Schemas are dynamically resolved at read time (reader fetches writer's schema from Registry). More flexible schema evolution than Protobuf.

**Where to Use:** Kafka event serialization with Schema Registry. When schema evolution flexibility (adding fields with defaults) is more important than raw speed.

**Advantages:**
- Rich schema types (unions, enums, records — more expressive than Protobuf)
- Dynamic schema resolution (reader and writer can have different schema versions)
- Schema evolution (add fields with defaults — backward compatible)
- JSON schema format (human-readable schema definition)
- Native to Hadoop/Kafka ecosystem (deep integration with big data tools)

**Use Cases:**
- Kafka event format (LinkedIn, Netflix — Avro for all events)
- Hadoop data files (Avro as the storage format with embedded schema)
- Data pipeline serialization (Spark/Flink reading/writing Avro)
- Schema-sensitive applications (where data format evolves over time)
- Cross-language data exchange (Java producer, Python consumer — schema bridges them)

---

### Amazon SNS
SNS is AWS's pub/sub notification service — publishers send to topics, multiple subscribers (SQS, Lambda, HTTP, email, SMS) receive copies simultaneously. At-least-once delivery. Common SNS+SQS fanout pattern: SNS fans out to multiple SQS queues.

**Where to Use:** Broadcasting events to multiple subscribers on AWS — fanout notifications, multi-consumer event distribution, mobile push notifications.

**Advantages:**
- Simple pub/sub (publish to topic — all subscribers get it)
- Fanout pattern (one event → SNS → multiple SQS queues for independent processing)
- Multiple transport (SQS, Lambda, HTTP/S, email, SMS — one publish, many channels)
- Message filtering (subscriber only receives messages matching attributes)
- Fully managed (serverless, auto-scales, pay-per-request)

**Use Cases:**
- Fanout event processing (order placed → SNS → email + inventory + analytics + fraud)
- Mobile push notifications (SNS → APNS/FCM for iOS/Android push)
- SMS notifications (transactional SMS via SNS)
- Event-driven architecture (SNS topic as the event bus between microservices)
- System alerts (CloudWatch alarm → SNS → PagerDuty/Slack/email)

---

### AWS Lambda
Lambda is serverless compute — upload code, AWS runs it on demand, pay per invocation. Auto-scales from 0 to thousands of concurrent executions. Triggers: API Gateway, S3 events, DynamoDB Streams, SQS, EventBridge. 15-minute timeout. Cold starts add 100-500ms.

**Where to Use:** Event-driven architectures, short-lived functions (<15 min), unpredictable traffic, API backends with variable load, file processing triggers.

**Advantages:**
- Zero infrastructure (no servers to provision, patch, or manage)
- Pay-per-use (pay only for execution time — zero cost when idle)
- Auto-scaling (0 to 10,000+ concurrent executions instantly)
- Event-driven (trigger on S3 upload, DB change, API call, schedule)
- Ecosystem integration (connects to every AWS service natively)

**Use Cases:**
- API backend (API Gateway → Lambda → DynamoDB — serverless REST API)
- File processing (S3 upload → Lambda → generate thumbnail + update DB)
- Database triggers (DynamoDB Streams → Lambda → update Elasticsearch index)
- Scheduled jobs (EventBridge cron → Lambda → nightly cleanup)
- Real-time stream processing (Kinesis → Lambda → process events)

---

### Apache HBase
HBase is a distributed NoSQL database on HDFS — Google Bigtable's open-source equivalent. Row key + column families, LSM-tree storage, strong consistency per row. Uses ZooKeeper for coordination and HDFS for 3x-replicated storage.

**Where to Use:** Hadoop ecosystem investments needing real-time read/write on big data. When you need Bigtable's model on-premise.

**Advantages:**
- Strong consistency per row (not eventual like Cassandra's default)
- Sits on HDFS (integrates with Hadoop ecosystem — Hive, Pig, Spark)
- Scales to billions of rows (handles petabyte-scale datasets)
- Real-time read/write on big data (random access on massive datasets)
- Coprocessors (run business logic on the data node — like stored procedures)

**Use Cases:**
- Real-time access to big data (Hadoop data lake with random read/write)
- Time-series on Hadoop (IoT, sensor data stored in HBase, analyzed with Spark)
- Facebook Messenger backend (historically — before custom solutions)
- Content management at scale (store and retrieve large structured datasets)
- Graph storage backing store (JanusGraph uses HBase for storage)

---

### ScyllaDB
ScyllaDB is a C++ rewrite of Cassandra — wire-compatible (CQL, same data model) but 5-10x better performance. Thread-per-core architecture (Seastar framework) with no JVM GC pauses.

**Where to Use:** When you need Cassandra's data model but with better latency consistency and no GC pauses. When Cassandra's JVM garbage collection causes latency spikes.

**Advantages:**
- 5-10x better performance than Cassandra on same hardware
- No GC pauses (C++ with thread-per-core — predictable low latency)
- Cassandra-compatible (CQL, same data model — drop-in replacement)
- Shard-per-core architecture (each core independently handles its data)
- Lower TCO (fewer nodes needed for same throughput)

**Use Cases:**
- Messaging backends (Discord — billions of messages, moved from Cassandra to ScyllaDB)
- Real-time bidding (ad-tech — need consistent sub-ms latency)
- IoT time-series at massive scale (better throughput per node)
- Gaming leaderboards and player state (low-latency reads/writes)
- Replacement for Cassandra when GC pauses are unacceptable

---

### CockroachDB
CockroachDB is a distributed SQL database with ACID across regions — "Spanner for everyone" (open-source, PostgreSQL-compatible). Uses Raft consensus, auto-ranges and rebalances. Global ACID with cross-region write latency ~100-200ms.

**Where to Use:** When you need global ACID transactions but can't use Spanner (GCP-only or too expensive). Multi-region applications needing strong consistency.

**Advantages:**
- Global ACID transactions (strong consistency across continents)
- PostgreSQL-compatible (drop-in replacement — same SQL, same drivers)
- Auto-sharding (splits and rebalances data automatically — no manual partitioning)
- Survives failures (any node can die — data is replicated via Raft)
- Open-source (self-host or use CockroachDB Cloud)

**Use Cases:**
- Global financial systems (multi-region money movement with ACID)
- Multi-region SaaS (user data consistent across all regions)
- Inventory management (accurate stock counts across global warehouses)
- Compliance-heavy applications (GDPR, financial regulations requiring ACID)
- Replacement for sharded MySQL/PostgreSQL (no manual sharding)

---

### CloudWatch
CloudWatch is AWS's monitoring service — collects metrics, logs, and traces. Alarms trigger on thresholds. Default monitoring for AWS resources (Lambda, EC2, RDS auto-send metrics). Less flexible than Prometheus+Grafana but zero setup.

**Where to Use:** AWS-native infrastructure monitoring — default for any AWS deployment. Use alongside Prometheus for application metrics.

**Advantages:**
- Zero setup (AWS services automatically send metrics — Lambda, EC2, RDS)
- Unified (metrics, logs, and traces in one service)
- Alarms (threshold-based → trigger auto-scaling, SNS, Lambda)
- Log Insights (SQL-like queries on log data — search and analyze)
- Deep AWS integration (correlate metrics with AWS resource events)

**Use Cases:**
- Infrastructure monitoring (CPU, memory, disk for EC2/ECS/EKS)
- Lambda monitoring (invocation count, duration, errors, cold starts)
- RDS monitoring (database connections, query latency, storage)
- Log aggregation (application logs from CloudWatch Logs → Log Insights)
- Auto-scaling triggers (CPU > 70% → scale out, queue depth > 1000 → add workers)

---

### OpenTelemetry
OpenTelemetry is a vendor-neutral observability standard (CNCF) — one SDK for metrics, logs, and traces, exportable to any backend (Jaeger, Prometheus, Datadog, Honeycomb). Auto-instruments HTTP, gRPC, database calls.

**Where to Use:** Any new application — instrument once with OpenTelemetry, switch observability backends without rewriting code. The standard for modern observability.

**Advantages:**
- Vendor-neutral (instrument once — export to Jaeger, Datadog, Honeycomb, Zipkin)
- Auto-instrumentation (HTTP, gRPC, DB calls automatically traced — minimal code changes)
- Unified (metrics + logs + traces in one SDK — not 3 separate SDKs)
- Context propagation (trace ID flows through all services automatically)
- CNCF standard (the industry is converging on OpenTelemetry)

**Use Cases:**
- Distributed tracing across microservices (one trace ID across 10 services)
- Vendor-agnostic observability (start with Jaeger, switch to Datadog without rewriting)
- Auto-instrumentation (add tracing to existing apps with minimal code changes)
- Correlation (connect metrics, logs, and traces via shared trace IDs)
- New project standard (every new microservice starts with OpenTelemetry SDK)

---

### TensorFlow / PyTorch
TensorFlow (Google) and PyTorch (Meta) are the two dominant deep learning frameworks. PyTorch uses dynamic graphs (define-by-run — intuitive debugging, 90% of research papers). TensorFlow uses Keras as high-level API, better production tooling (TF Serving, TF Lite, TF.js).

**Where to Use:** PyTorch for research, experimentation, and model development. TensorFlow for production deployment on mobile (TF Lite), browser (TF.js), or serving (TF Serving).

**Advantages:**
- PyTorch: Dynamic computation graph (intuitive debugging with standard Python)
- PyTorch: Dominant in research (90% of new papers — largest community for cutting-edge)
- TensorFlow: Production deployment tooling (TF Serving, TF Lite for mobile, TF.js for browser)
- TensorFlow: Keras high-level API (simple model definition — few lines for standard architectures)
- Both: GPU/TPU acceleration, distributed training, pre-trained models (HuggingFace)

**Use Cases:**
- Model training (PyTorch for R&D — train CNNs, Transformers, LLMs)
- Production inference (TF Serving or TorchServe — serve trained models behind API)
- Mobile ML (TF Lite — run models on iOS/Android with low latency)
- Browser ML (TF.js — run models in browser, no server needed)
- Google products use TensorFlow/JAX; Meta products use PyTorch exclusively

---

### Long Polling
Long polling is a legacy real-time technique — client sends HTTP request, server holds it open until data is available or timeout, then client immediately re-requests. Before WebSocket, this was the standard (Facebook Chat, early Gmail Chat).

**Where to Use:** Only as a fallback when WebSocket is blocked by strict corporate proxies. Strictly worse than WebSocket/SSE for new applications.

**Advantages:**
- Works over standard HTTP (no protocol upgrade — works through all proxies)
- No special server requirements (any HTTP server can implement it)
- Simpler than WebSocket for one-way updates (if WebSocket isn't available)
- Fallback for restrictive environments (corporate firewalls blocking WebSocket)

**Use Cases:**
- Legacy systems (old browsers that don't support WebSocket)
- Corporate proxy environments (strict firewalls that block WebSocket upgrade)
- Simple notification systems (server has occasional updates — not worth WebSocket complexity)
- Fallback mechanism (try WebSocket first, fall back to long polling if blocked)

---

### QUIC / HTTP/3
QUIC is a transport protocol on UDP — solves TCP's head-of-line blocking (if one packet lost, only that stream stalls, not all streams). Combines transport + TLS handshake (1-RTT first connection, 0-RTT returning). Connection migration (WiFi→cellular doesn't drop connection — identified by connection ID, not IP).

**Where to Use:** Modern web applications needing the lowest possible connection setup latency and resilience to network changes. Supported by Google, Cloudflare, Facebook.

**Advantages:**
- No head-of-line blocking (stream-level multiplexing over UDP — one lost packet doesn't stall others)
- 0-RTT connection setup (returning connections resume instantly with cached keys)
- Connection migration (switch WiFi to cellular — connection survives, no reconnect)
- Better on lossy networks (mobile, unstable WiFi — QUIC handles packet loss better)
- Built-in encryption (TLS 1.3 integrated — no separate TLS layer)

**Use Cases:**
- Google services (Search, YouTube, Gmail — all support QUIC/HTTP/3)
- Video streaming (YouTube — faster start, fewer buffering on mobile)
- Mobile applications (QUIC handles network switching and lossy connections better)
- Gaming and real-time (lower latency, connection survives network changes)
- CDN edge delivery (Cloudflare supports HTTP/3 — faster content delivery)

---

### XMPP
XMPP is an XML-based protocol for instant messaging (Jabber, Google Talk) — federated model (like email), anyone can run a server. Three stanza types: message, presence, IQ. WhatsApp originally used customized XMPP before moving to a custom protocol.

**Where to Use:** Federated communication systems (like email but for chat), enterprise chat (Cisco Jabber), HIPAA-compliant messaging. Largely replaced by WebSocket + binary protocols.

**Advantages:**
- Federated (anyone can run a server — like email, no single provider controls it)
- Presence built-in (online/offline status is a first-class feature)
- Rich extension model (XEPs — add features like file transfer, voice, video)
- Mature and standardized (IETF standard since 2004)
- Open (no vendor lock-in — multiple server and client implementations)

**Use Cases:**
- Enterprise chat (Cisco Jabber, some HIPAA-compliant systems)
- Federated social networks (some decentralized platforms use XMPP)
- IoT device communication (some industrial IoT uses XMPP for presence/messaging)
- Legacy system support (existing XMPP infrastructure still operational)
- Real-time notification systems (Jira, Jenkins use XMPP for notifications)

---

### Celery
Celery is a Python task queue library — define tasks as decorated functions (@app.task), call via task.delay() which pushes to a broker (Redis or RabbitMQ). Workers pick up and execute. Supports retries with backoff, chaining, groups, rate limiting, and scheduled tasks (Celery Beat).

**Where to Use:** Python/Django/Flask applications needing background task processing — sending emails, image processing, report generation, any operation taking >200ms.

**Advantages:**
- Python-native (define tasks as regular Python functions — no new language)
- Rich task management (retries, chaining, groups, rate limiting, scheduling)
- Multiple brokers (Redis or RabbitMQ — choose based on existing infrastructure)
- Result tracking (check task status, get return value from result backend)
- Celery Beat (cron-like scheduling — run tasks on a recurring schedule)

**Use Cases:**
- Background email sending (user registers → background task sends welcome email)
- Image/video processing (upload → background task generates thumbnails)
- Report generation (user requests report → background task compiles PDF)
- Web scraping (scheduled tasks scrape data and store results)
- ML model inference (background task runs model prediction and stores result)

---

### NATS
NATS is a lightweight, high-performance messaging system — simpler than Kafka (no persistence by default, no partitions), much faster (sub-millisecond latency, millions/sec). Subject-based routing with wildcards. JetStream adds Kafka-like durability.

**Where to Use:** Microservice communication needing ultra-low latency, IoT device messaging, cloud-native applications wanting simple messaging without Kafka's operational burden.

**Advantages:**
- Sub-millisecond latency (much faster than Kafka — no disk persistence by default)
- Simple subject routing ("orders.created", "orders.*" — intuitive pub/sub)
- JetStream adds durability (Kafka-like streams, consumers, exactly-once)
- Leaf nodes (edge messaging — run local NATS that connects to central cluster)
- Tiny footprint (single binary, ~20MB — minimal resources)

**Use Cases:**
- Microservice messaging (request-reply, pub/sub between services)
- IoT device communication (lightweight protocol for constrained devices)
- Real-time gaming (ultra-low-latency message routing)
- Financial trading (low-latency order routing and market data distribution)
- Edge computing (leaf nodes for disconnected edge operations that sync to central)

---

### Apache Storm / Samza
Storm is Twitter's real-time stream processing framework (spouts → bolts topology). Samza is LinkedIn's Kafka-integrated stream processor (local RocksDB state). Both largely superseded by Flink.

**Where to Use:** Primarily for understanding existing systems at Twitter (Storm) or LinkedIn (Samza). For new stream processing, use Flink or Spark Structured Streaming.

**Advantages:**
- Storm: At-least-once processing with guaranteed message processing
- Storm: Flexible topology (spouts read, bolts process — DAG of operations)
- Samza: Kafka-native (built for Kafka, uses Kafka for input and output)
- Samza: Local state (RocksDB per task — stateful processing without external DB)
- Both: Proven at scale (Twitter/LinkedIn processed billions of events/day)

**Use Cases:**
- Legacy stream processing (existing Storm/Samza topologies — maintain and optimize)
- Understanding Twitter/LinkedIn architecture (interview discussions about their systems)
- Transitioning to Flink (understand the concepts before migrating)
- Real-time ETL (Storm for transform-and-load streaming pipelines)
- Real-time analytics (Samza for per-user sessionization and aggregation)

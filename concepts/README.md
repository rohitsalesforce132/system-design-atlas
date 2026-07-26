# System Design Building Blocks — Summary

This section covers foundational concepts that apply to EVERY system in this repo. Read these first — they'll make the app-specific writeups much easier to understand.

| Concept | File | What You'll Learn |
|---------|------|------------------|
| Load Balancing | [load-balancing.md](load-balancing.md) | How traffic is distributed across servers |
| Caching | [caching.md](caching.md) | How to make reads 100x faster |
| Database Scaling | [database-scaling.md](database-scaling.md) | Sharding, replication, SQL vs NoSQL |
| Message Queues | [message-queues.md](message-queues.md) | Async processing, Kafka, decoupling |
| CDN | [cdn.md](cdn.md) | Content delivery at global scale |
| Microservices | [microservices.md](microservices.md) | When and how to split a monolith |
| Consistency Models | [consistency-models.md](consistency-models.md) | ACID vs BASE, CAP/PACELC, strong vs eventual, tunable consistency |

## The 10 Most Common Patterns

Every system in this repo uses some combination of these patterns. Understand these and you understand 80% of system design.

```
1. Load Balancer + Multiple Servers    →  horizontal scaling
2. Cache + Database                    →  fast reads
3. Database Sharding                   →  data scaling
4. CDN + Origin Server                 →  global content delivery
5. Message Queue + Workers             →  async processing
6. API Gateway + Microservices         →  service decomposition
7. Read Replicas                       →  read scaling
8. WebSocket / Server-Sent Events      →  real-time communication
9. Search Index (Elasticsearch)        →  fast full-text search
10. Graph Database                     →  relationship-heavy data (social)
```

## The Scaling Ladder

As your app grows, you climb this ladder. Each rung adds complexity but multiplies capacity.

```
Level 0: Single Server
  └── One machine runs everything (app + database + static files)
  Capacity: ~1,000 users

Level 1: App + DB Separation
  └── Separate database server from app server
  Capacity: ~10,000 users

Level 2: Vertical Scaling + Cache
  └── Bigger servers + Redis cache + CDN
  Capacity: ~100,000 users

Level 3: Horizontal Scaling
  └── Multiple app servers + load balancer + read replicas
  Capacity: ~1,000,000 users

Level 4: Sharding + Microservices
  └── Shard databases + extract microservices
  Capacity: ~10,000,000 users

Level 5: Multi-Region + Global CDN
  └── Active-active multi-region + global CDN + edge computing
  Capacity: ~100,000,000+ users

Level 6: Custom Infrastructure
  └── Custom databases, custom protocols, custom hardware
  Capacity: ~1,000,000,000+ users (Netflix, YouTube, WhatsApp level)
```

## Quick Reference: Which Database?

```
Need transactions (money, orders)?     → PostgreSQL / MySQL
Need massive scale + simple lookups?    → Cassandra / DynamoDB
Need flexible schema?                   → MongoDB
Need full-text search?                  → Elasticsearch
Need social relationships?              → Neo4j (graph DB)
Need real-time counters/leaderboards?   → Redis (sorted sets)
Need time-series data (metrics)?        → InfluxDB / TimescaleDB
Need blob storage (images, video)?      → S3 / object storage
Need message persistence?               → Kafka
```

## Quick Reference: Which Message Queue?

```
Need massive throughput (millions/sec)?  → Kafka
Need flexible routing?                   → RabbitMQ
Need simple queue + already use Redis?   → Redis Streams
Need fully managed (no ops)?             → SQS / Pub/Sub
Need ultra-low latency?                  → NATS / Aeron
```

## Quick Reference: Which Cache Pattern?

```
Read-heavy, data doesn't change often?     → Cache-Aside (Lazy Loading)
Must never serve stale data?               → Write-Through
Need ultra-fast writes (likes, views)?     → Write-Behind
Session data?                              → Redis with TTL
```

## The One Concept to Remember

Every system design decision is a **trade-off**:

```
Latency      ←→  Consistency
Simplicity   ←→  Scalability
Cost         ←→  Availability
Accuracy     ←→  Performance

There are no perfect solutions — only trade-offs for your specific needs.
```

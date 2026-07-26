# Components & Technologies — Complete Reference

> Every technology, database, protocol, and tool used across all 22 system designs — explained from scratch with alternatives and comparisons.

---

## What's Inside

| Guide | What You Learn |
|-------|---------------|
| [Databases](databases.md) | Redis, MySQL, PostgreSQL, Cassandra, DynamoDB, Bigtable, Elasticsearch, MongoDB, S3, Spanner, ClickHouse, Snowflake, Neo4j, SQLite — what each is, how they work internally, when to use, alternatives |
| [Messaging & Streaming](messaging.md) | Kafka, RabbitMQ, SQS, Redis Streams, NATS, Pulsar, Celery — queues vs topics, delivery guarantees, partitioning, consumer groups |
| [Networking & Protocols](networking.md) | WebSocket, SSE, gRPC, WebRTC, MQTT, HTTP/2, QUIC, XMPP — connection lifecycles, handshakes, when to use each |
| [API Architectures](api-architectures.md) | REST, GraphQL, gRPC, Protobuf — request-response vs streaming, over/under-fetching, decision tree |
| [Infrastructure & DevOps](infrastructure-and-devops.md) | Load Balancers (Nginx, HAProxy, Envoy), CDN (CloudFront, Akamai, Cloudflare), Kubernetes, Data Processing (Spark, Flink), Monitoring (Prometheus, Grafana, Jaeger) |

---

## Quick Reference: Which Database?

```
Need transactions (money, orders)?     → PostgreSQL / MySQL
Need massive scale + simple lookups?    → Cassandra / DynamoDB
Need flexible schema?                   → MongoDB
Need full-text search?                  → Elasticsearch
Need social relationships?              → Neo4j (graph DB)
Need real-time counters/leaderboards?   → Redis (sorted sets)
Need time-series data (metrics)?        → ClickHouse / TimescaleDB
Need blob storage (images, video)?      → S3 / object storage
Need message persistence?               → Kafka
Need global ACID across regions?        → Spanner
Need analytics over billions of rows?   → ClickHouse / Snowflake
Need embedded on-device storage?        → SQLite
```

## Quick Reference: Which Messaging System?

```
Need massive throughput (millions/sec)?  → Kafka
Need flexible routing?                   → RabbitMQ
Need simple queue + already use Redis?   → Redis Streams
Need fully managed (no ops)?             → SQS / Pub/Sub
Need ultra-low latency?                  → NATS / Aeron
```

## Quick Reference: Which Communication Protocol?

```
Client requests data from server?        → REST / GraphQL / gRPC
Server pushes to client (one-way)?       → SSE
Bidirectional real-time?                 → WebSocket
Video/audio calls?                       → WebRTC
IoT devices (low bandwidth)?             → MQTT
Service-to-service (internal)?           → gRPC
```

## Quick Reference: Which Load Balancer?

```
Simple reverse proxy + static files?     → Nginx
Layer 4 raw throughput?                  → HAProxy / AWS NLB
Microservices service mesh?              → Envoy
API gateway (auth, rate limit)?          → Kong / AWS ALB
Custom Netflix-scale?                    → Zuul
```

## Quick Reference: Which Data Processing?

```
Batch processing (Hadoop-style)?         → Spark / MapReduce
Stream processing (real-time)?           → Flink / Storm
Ad-hoc SQL analytics?                    → Presto / Trino
Workflow orchestration?                  → Airflow
Real-time dashboards?                    → ClickHouse + Grafana
```

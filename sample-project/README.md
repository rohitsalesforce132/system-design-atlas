# 🚀 NebulaShop — Full-Stack Sample Project

> A mini social-commerce platform demonstrating **every major component** from the System Design Atlas in a single, runnable project.

---

## What This Project Demonstrates

```
┌─────────────────────────────────────────────────────────────────┐
│                     NebulaShop Architecture                      │
│                                                                 │
│  Browser ──► Nginx (Load Balancer / Reverse Proxy)             │
│                │                                                │
│      ┌─────────┼─────────┐                                     │
│      ▼         ▼         ▼                                     │
│   API Server  Realtime   Static Files                           │
│   (Flask)     (WebSocket)  (HTML/JS)                            │
│      │         │                                                │
│      │    Redis Pub/Sub ◄──┘  (real-time message bus)          │
│      │                                                          │
│      ├──► PostgreSQL (primary database — users, posts, orders) │
│      ├──► Redis (cache — sessions, rate limiting, hot data)    │
│      ├──► Elasticsearch (full-text search)                      │
│      ├──► Kafka (event streaming — async processing)           │
│      └──► S3/MinIO (object storage — file uploads)             │
│                                                                 │
│  Worker Service (Kafka consumer):                               │
│    ├── Indexes new posts to Elasticsearch                      │
│    ├── Sends notifications via Redis Pub/Sub                   │
│    └── Updates analytics counters                              │
│                                                                 │
│  Monitoring:                                                    │
│    ├── Prometheus (metrics scraping)                           │
│    └── Grafana (dashboards + alerts)                           │
└─────────────────────────────────────────────────────────────────┘
```

## Components Used

| Component | Technology | Role in This Project |
|-----------|-----------|---------------------|
| **Load Balancer** | Nginx | Reverse proxy, routing, static file serving |
| **API Server** | Python Flask | REST API — CRUD operations, auth |
| **Real-Time Server** | Python websockets | WebSocket for live feed + notifications |
| **Database** | PostgreSQL 16 | Primary data store — users, posts, orders |
| **Cache** | Redis 7 | Session cache, rate limiting, hot data cache |
| **Search** | Elasticsearch 8 | Full-text search across posts and products |
| **Message Queue** | Kafka (via Redpanda) | Event streaming — post created, order placed |
| **Object Storage** | MinIO (S3-compatible) | File uploads (images, avatars) |
| **Metrics** | Prometheus | Infrastructure and app metrics |
| **Dashboards** | Grafana | Visual monitoring dashboards |
| **Real-Time Bus** | Redis Pub/Sub | Message routing between WebSocket servers |

---

## Quick Start

```bash
# Clone the repo
cd system-design-atlas/sample-project

# Start ALL services (first run takes 2-3 minutes for images)
docker-compose up -d

# Initialize database + Elasticsearch
./scripts/init.sh

# Open the app
open http://localhost:8080

# Services:
#   Web App:        http://localhost:8080
#   API:            http://localhost:8080/api
#   WebSocket:      ws://localhost:8080/ws
#   Grafana:        http://localhost:3001  (admin/admin)
#   Prometheus:     http://localhost:9091
#   MinIO Console:  http://localhost:9001  (minioadmin/minioadmin)
```

## API Endpoints

```bash
# Create a user
curl -X POST http://localhost:8080/api/users \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@test.com"}'

# Create a post
curl -X POST http://localhost:8080/api/posts \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"title":"My First Post","content":"Hello NebulaShop!"}'

# Search posts (Elasticsearch)
curl "http://localhost:8080/api/search?q=hello"

# Get cached user (Redis)
curl http://localhost:8080/api/users/1

# Place an order (triggers Kafka event)
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"product":"Coffee Mug","quantity":2,"price":499}'
```

## What Happens When You Create a Post

```
1. POST /api/posts → API Server
2. API writes to PostgreSQL (source of truth)
3. API publishes event to Kafka: "post.created"
4. API returns 201 Created to user (fast — no waiting for Kafka)
5. Worker (Kafka consumer) picks up "post.created":
   a. Indexes the post in Elasticsearch (now searchable)
   b. Publishes notification to Redis Pub/Sub
6. Realtime server (subscribed to Redis Pub/Sub):
   → Pushes notification via WebSocket to connected users
7. User sees new post appear in real-time feed (no refresh!)
```

## Stopping & Cleanup

```bash
# Stop all services
docker-compose down

# Stop + remove all data (fresh start)
docker-compose down -v
```

# Sample Architectures — Practical Design Blueprints

> These are **not** analyses of existing apps (see `apps/` for those). These are **from-scratch design blueprints** — how to architect common system types, with component selection rationale, schema design, API design, step-by-step request flows, and trade-off analysis.

---

## What's Inside

Each architecture follows the same 10-section structure:

1. **Problem Statement & Requirements** — functional + non-functional
2. **Capacity Estimation** — QPS, storage, bandwidth, compute (with math)
3. **High-Level Architecture** — ASCII diagram with every component
4. **Component Selection** — WHY each technology was chosen + alternatives
5. **Database Schema** — tables, indexes, relationships
6. **API Design** — endpoints, request/response formats
7. **Request Flow** — step-by-step walkthrough of main user action
8. **Scaling Strategy** — bottlenecks and solutions
9. **Failure Modes** — what breaks and how to handle it
10. **Trade-off Analysis** — key decisions and their costs

---

## Architecture Catalog

| # | Architecture | Key Challenge | Main Components |
|---|-------------|---------------|-----------------|
| 1 | [E-Commerce Platform](ecommerce-platform.md) | Flash sale traffic, inventory consistency | PostgreSQL, Redis, Elasticsearch, Kafka, S3 |
| 2 | [Real-Time Chat](realtime-chat.md) | WebSocket scaling, message delivery | Erlang/Go, Redis Pub/Sub, Cassandra, WebSocket |
| 3 | [Video Streaming](video-streaming.md) | Upload → transcode → stream pipeline | S3, Kafka, FFmpeg workers, CDN, ClickHouse |
| 4 | [Notification System](notification-system.md) | Multi-channel fanout, delivery tracking | Kafka, Redis, SES/SNS/Twilio, PostgreSQL |
| 5 | [URL Shortener](url-shortener.md) | High read:write ratio, short code generation | Redis, PostgreSQL, Base62, CDN |
| 6 | [Rate Limiter](rate-limiter.md) | Distributed coordination, algorithms | Redis, Lua scripts, token bucket, sliding window |
| 7 | [Event-Driven Microservices](event-driven-microservices.md) | Saga pattern, CQRS, event sourcing | Kafka, PostgreSQL, Redis, gRPC |
| 8 | [Multi-Tenant SaaS](multi-tenant-saas.md) | Tenant isolation, billing, data residency | PostgreSQL, Redis, Kubernetes, Stripe |

---

## How These Differ from `apps/` Deep Dives

```
apps/ folder:                     architectures/ folder:
─────────────────                ──────────────────────
"How does WhatsApp work?"         "How do I design a chat app?"

Analyzes EXISTING apps.           Designs FROM SCRATCH.
What they built.                  What you should build.
Tech stack they chose.            Rationale for choosing tech.
Real scale numbers.               Estimated capacity (with math).
"How they scale."                 "How to scale (step by step)."
```

---

## The Universal Architecture Pattern

Every architecture in this folder is a variation of this universal pattern:

```
┌─────────────────────────────────────────────────────────────┐
│                     THE UNIVERSAL STACK                      │
│                                                              │
│  Users                                                      │
│    │                                                         │
│    ▼                                                         │
│  CDN (static: images, CSS, JS — Cloudflare/CloudFront)     │
│    │                                                         │
│    ▼                                                         │
│  DNS (GeoDNS → nearest data center)                         │
│    │                                                         │
│    ▼                                                         │
│  Load Balancer (traffic distribution — Nginx/ALB)          │
│    │                                                         │
│    ▼                                                         │
│  API Gateway (auth, rate limit, routing — Kong/Envoy)      │
│    │                                                         │
│    ▼                                                         │
│  Microservices (User, Order, Search, Notification...)      │
│    │                                                         │
│    ▼                                                         │
│  Cache Layer (Redis — hot data, sessions)                   │
│    │                                                         │
│    ▼                                                         │
│  Database (PostgreSQL/MySQL — source of truth)             │
│    │                                                         │
│    ▼                                                         │
│  Message Queue (Kafka — async processing, decoupling)      │
│    │                                                         │
│    ▼                                                         │
│  Workers (transcoding, analytics, notifications)            │
└─────────────────────────────────────────────────────────────┘
```

The magic is in **what each architecture customizes** for its specific problem.

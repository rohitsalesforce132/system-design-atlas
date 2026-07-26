# Microservices Architecture

## What It Is (Analogy First)

Imagine a restaurant where one person takes orders, cooks food, washes dishes, and processes payment. That's a **monolith** — one system doing everything.

Now imagine a restaurant with specialized roles: host (seating), waiter (orders), chef (cooking), bartender (drinks), cashier (payment). Each is a **microservice** — specialized, independent, and replaceable.

```
MONOLITH:
┌─────────────────────────────────────┐
│           Single Application         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐
│  │ Orders   │  │  Users  │  │ Payment │
│  │ Module   │  │ Module  │  │ Module  │
│  └─────────┘  └─────────┘  └─────────┘
│         (All in one process)         │
│  (If payment bug crashes app →       │
│   ordering also goes down)           │
└─────────────────────────────────────┘

MICROSERVICES:
  ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐
  │ Order   │   │  User  │   │Payment │   │Shipping│
  │ Service │   │ Service│   │ Service│   │ Service│
  │(Node.js)│   │ (Go)   │   │(Java)  │   │(Python)│
  └────┬───┘   └────┬───┘   └────┬───┘   └────┬───┘
       │             │            │            │
       ▼             ▼            ▼            ▼
  ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐
  │Orders DB│   │Users DB│   │Payment │   │Shipping│
  │(Postgres)│  │(MySQL) │   │DB(Cassandra)│DB(Mongo)│
  └────────┘   └────────┪   └────────┘   └────────┘
```

## Monolith vs Microservices

| Aspect | Monolith | Microservices |
|--------|---------|---------------|
| **Deployment** | One unit | Each service deployed independently |
| **Tech Stack** | One language/framework | Each service can use different tech |
| **Scaling** | Scale entire app | Scale only the service that needs it |
| **Team** | Everyone in one codebase | Teams own their service end-to-end |
| **Failure** | One bug can crash everything | Failures isolated to one service |
| **Complexity** | Low | High (network, distributed systems) |
| **Speed** | Simple to build and deploy | Complex but faster at scale |
| **Data** | Shared database | Each service owns its data |

## When to Use Microservices (And When NOT To)

### Start as a Monolith (Almost Always)
```
Phase 1 (0-100K users):    Monolith — fast to build, easy to understand
Phase 2 (100K-1M users):   Monolith + cache + read replicas
Phase 3 (1M-10M users):    Extract first microservices (the ones under most load)
Phase 4 (10M+ users):      Full microservices architecture
```

**Rule of thumb:** Don't start with microservices. Start monolithic and extract services as specific pain points emerge. You will know when to split.
```
```

## The Microservices Trade

Microservices solve problems but create new ones:

```
What you gain:              What you must handle:
──────────────────────────────────────────────────
Independent deploys         Network calls replace function calls
Independent scaling          Data is now distributed
Tech flexibility             Must implement service discovery
Fault isolation             Must handle partial failures
Team autonomy               Must implement distributed tracing
                            Must implement API versioning
                            Must implement circuit breakers
                            Must handle distributed transactions
```

**The hardest part of microservices is not building them — it's operating them.**

## Core Components of a Microservices System

```
                    ┌──────────────┐
  User ──► API Gateway ──► [Service Discovery] ◄── knows where each service is
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
  [Service A]  [Service B]  [Service C]
       │            │            │
       │     ┌──────┘      ┌─────┘
       ▼     ▼             ▼
  [Message Queue] (async communication)
       │
       ▼
  [Event Bus / Kafka]
       │
  ┌────┼────────────┐
  ▼    ▼            ▼
[Analytics] [Notification] [Audit]
```

### 1. API Gateway
The single entry point. Handles:
- **Routing:** `/api/users/*` → User Service, `/api/orders/*` → Order Service
- **Authentication:** Verify JWT token once, pass user info to services
- **Rate limiting:** 100 req/min per user
- **Load balancing:** Distribute across service instances
- **SSL termination:** Decrypt HTTPS once at gateway
- **Response composition:** Combine responses from multiple services into one response

```
User ──► API Gateway
              │
              ├──► Auth Service (verify token)
              ├──► User Service (get profile)
              ├──► Order Service (get orders)
              │
              └──► Combine all responses → return to user
```

### 2. Service Discovery
In a dynamic environment, services start and stop constantly. How does Service A find Service B?

```
Service B starts → registers itself: "I'm order-service, at 10.0.0.15:8080"

Service A needs to call order-service:
  1. Ask Service Registry: "Where is order-service?"
  2. Registry responds: "10.0.0.15:8080"
  3. Service A calls 10.0.0.15:8080
```

**Tools:** Consul, etcd, Kubernetes DNS, AWS Cloud Map.

### 3. Inter-Service Communication

#### Synchronous (HTTP/gRPC)
```
Service A ──HTTP POST──► Service B
  A waits for B's response.
  If B is slow, A is blocked.
```
- Use when you need an immediate response.
- Risk: Cascading failures (B's slowness cascades to A, then to A's callers).

#### Asynchronous (Message Queue / Events)
```
Service A ──► Kafka topic ──► Service B
  A doesn't wait. B processes when ready.
```
- Use for fire-and-forget events.
- Decouples services.
- Better resilience.

### 4. Circuit Breaker
Protects against cascading failures.

```
Normal:      Service A ──► Service B ──► 200 OK
Degraded:    Service A ──► Service B ──► timeout (B is slow)
Failing:     Service A ──► Service B ──► timeout, timeout, timeout

Circuit Breaker activates:
  After 5 consecutive failures → OPEN (stop calling B)
  → Return fallback response immediately
  → Wait 30 seconds
  → HALF-OPEN (try one request to see if B recovered)
  → If success → CLOSED (normal operation)
  → If fail → OPEN again
```

**States:**
```
CLOSED ──(many failures)──► OPEN ──(after timeout)──► HALF-OPEN
  ▲                                                        │
  └──────────────(success)────────────────────────────────┘
```

### 5. Distributed Tracing
In a monolith, you trace a request through function calls. In microservices, a request hops across multiple services. How do you debug?

```
Request ID: abc-123

User ──► Gateway (trace: abc-123)
              │
              ├──► Auth Service (trace: abc-123, span: auth, 5ms)
              ├──► User Service (trace: abc-123, span: get-profile, 12ms)
              └──► Order Service (trace: abc-123, span: get-orders, 45ms)
                      └──► Inventory (trace: abc-123, span: check-stock, 30ms)

Trace visualization:
  Gateway: ████████████████████████████████████████████ 62ms total
    Auth:  ████ 5ms
    User:  ████████ 12ms
    Order: ██████████████████████████████ 45ms
      └── Inventory: ██████████████████████████ 30ms
```

Every service adds the same trace ID to logs. Tools: Jaeger, Zipkin, OpenTelemetry.

## Data in Microservices

### Rule: Each Service Owns Its Data
```
BAD (shared database):
  Order Service ──┐
  User Service  ──┼──► Shared PostgreSQL
  Payment Service─┘

  Problem: tight coupling. If Order Service changes schema, others break.

GOOD (database per service):
  Order Service   ──► Orders DB (PostgreSQL)
  User Service    ──► Users DB (MySQL)
  Payment Service ──► Payments DB (Cassandra)
```

### When Services Need Each Other's Data

**Option 1: Synchronous API call**
```
Order Service needs user's email:
  Order Service ──HTTP GET /users/123──► User Service ──► "alice@email.com"
```
Creates runtime dependency. If User Service is down, Order Service fails.

**Option 2: Event-driven data replication**
```
User updates email → User Service publishes event → Kafka
  → Order Service listens → updates its local copy of user data

Order Service has a read-only copy of needed user fields.
No runtime dependency. But data may be slightly stale.
```

### The Saga Pattern (Distributed Transactions)
```
Problem: Booking a trip requires:
  1. Book flight (Flight Service)
  2. Book hotel (Hotel Service)
  3. Book car (Car Service)
  4. Charge card (Payment Service)

If step 3 fails, must undo steps 1 and 2. No ACID across services.

Saga Solution: Chain of local transactions with compensating actions

  Book Flight ──success──► Book Hotel ──success──► Book Car ──FAIL
                                                                  │
  ◄── Compensate (cancel hotel) ◄── Compensate (cancel flight) ◄──┘
```

## How Real Companies Do Microservices

| Company | # Services | API Gateway | Service Discovery | Tracing |
|---------|-----------|-------------|-------------------|---------|
| **Netflix** | 500+ | Zuul | Eureka | Jaeger |
| **Amazon** | 1000s | AWS API Gateway | internal | X-Ray |
| **Uber** | 2,200+ | Envoy | Hyperbahn | Jaeger |
| **Twitter** | 100+ | custom | internal | Zipkin |
| **Spotify** | 100s | custom | Apollo | OpenTelemetry |

### Netflix Example (The Gold Standard)
```
Device (TV, Phone, Web)
  │
  ▼
AWS ELB (Load Balancer)
  │
  ▼
Zuul (API Gateway)
  ├── Auth (verify Netflix account)
  ├── Rate limiting
  └── Routing
  │
  ▼
Microservices (500+):
  ├── Recommendation Service
  ├── Playback State Service
  ├── Subtitle Service
  ├── Browse Service
  ├── Search Service
  ├── User Profile Service
  └── ...
  │
  ▼
Data Layer:
  ├── Cassandra (viewing history, massive scale)
  ├── EVCache (distributed cache)
  ├── S3 (movie files)
  └── Kafka (event streaming)
```

## How YOU Can Build This

### Level 1: Monolith (Start Here)
```
Node.js/Flask/Django app
  ├── /api/users
  ├── /api/orders
  └── /api/payments
Single database.
```

### Level 2: Extract First Service
```
Monolith ──► Message Queue ──► Notification Service (extracted)
  (handles users, orders)
                              (sends emails, SMS, push)
```
Extract the simplest, most isolated service first. Notification is a classic first extraction.

### Level 3: Full Microservices
```
                    ┌──────────────┐
  User ──► Nginx/Kong (API Gateway)
                    └──────┬───────┘
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         User Service  Order Service  Payment Service
         (Node.js)     (Python)       (Java)
              │            │            │
              ▼            ▼            ▼
         PostgreSQL    MongoDB     Cassandra
              │            │
              └────────────┘
                    │
                    ▼
              Redis / RabbitMQ (async events between services)
```

## Common Interview Questions

**Q: Should I start with microservices?**
A: **No.** Almost universally, start with a monolith. Microservices add massive operational complexity — service discovery, distributed tracing, network failures, data consistency. Only adopt when:
1. Your monolith is too large for one team to understand.
2. Different parts need different scaling characteristics.
3. Different teams need independent deploy cycles.

**Q: How do you handle a service being slow or down?**
A: Layered defense:
1. **Circuit breaker:** Stop calling failing service, return fallback.
2. **Timeouts:** Don't wait more than N seconds. Fail fast.
3. **Bulkheads:** Isolate resources per service so one failure doesn't consume all threads.
4. **Graceful degradation:** Return partial results (e: skip recommendations if rec service is down, still show browse page).

**Q: API Gateway — do you always need one?**
A: At scale, yes. It centralizes auth, routing, rate limiting. Without it, each service must implement these independently. But for 2-3 services, a simple Nginx reverse proxy may suffice.

**Q: How do you debug across services?**
A: Distributed tracing. Every request gets a unique trace ID at the gateway. Each service logs this ID. Tools like Jaeger or Zipkin reconstruct the full call chain with timing.

**Q: What is the Saga pattern?**
A: A way to maintain data consistency across microservices without distributed ACID transactions. Each step is a local transaction. If a later step fails, compensating transactions undo previous steps. Like an undo chain.
```

| System | Type | Key Constraint |
|--------|------|---------------|
| **WhatsApp** | Chat | Low latency messaging (<500ms) |
| **YouTube** | Content | Video upload + streaming + recommendations |
| **Netflix** | Streaming | Video delivery at scale + recommendations |
| **Uber** | Real-time | Matching riders and drivers in seconds |
| **Amazon** | E-commerce | Product search + checkout + inventory |
| **Google Search** | Search | Indexing the entire internet |
| **Facebook** | Social Network | Social graph + news feed |
| **Instagram** | Media | Photo/video storage + feed |
| **Twitter** | Social | Timeline generation at scale |
| **TikTok** | Video | Video recommendation algorithm |
| **Google Maps** | Navigation | Route calculation + real-time traffic |
| **Zoom** | Video | Real-time video conferencing |
| **Spotify** | Audio | Music streaming + recommendations |
| **Airbnb** | Marketplace | Search + booking + host management |

# API Architectures — REST, GraphQL, gRPC & Protocols

> How apps talk to each other and to clients. Every API style used across the System Design Atlas.

---

## Table of Contents

1. [The Big Picture](#big-picture)
2. [REST](#rest) — The default
3. [GraphQL](#graphql) — Client-driven queries
4. [gRPC](#grpc) — High-performance RPC
5. [Protocol Buffers](#protobuf) — Serialization format
6. [WebSocket](#websocket) — Bidirectional real-time
7. [Server-Sent Events (SSE)](#sse) — One-way streaming
8. [Long Polling](#long-polling) — The legacy fallback
9. [Comparison Table](#comparison)

---

<a id="big-picture"></a>
## The Big Picture

```
WHEN TO USE WHAT:

  Client needs data from server?
  └── Simple, standard?          → REST
  └── Need exactly specific fields, nested data? → GraphQL
  └── Service-to-service, low latency? → gRPC

  Server needs to push data to client?
  └── Bidirectional (chat, gaming)? → WebSocket
  └── One-way (notifications, live scores)? → SSE
  └── Legacy browser, no WebSocket? → Long Polling

  Real-time video/audio?
  └── → WebRTC (covered in networking.md)
```

---

<a id="rest"></a>
## REST — Representational State Transfer

### What It Is (Analogy)

REST is like a **library**. You go to a specific section (URL), pick up a book (GET), or submit a new book (POST). Each resource has a unique address. The librarian doesn't remember you between visits (stateless).

### How It Works

```
REST maps HTTP verbs to CRUD operations:

  GET    /users          → List all users      (Read)
  GET    /users/123      → Get one user         (Read)
  POST   /users          → Create a new user    (Create)
  PUT    /users/123      → Update entire user   (Update)
  PATCH  /users/123      → Partial update       (Update)
  DELETE /users/123      → Delete user          (Delete)

Stateless: Each request contains everything needed.
  No server-side session needed (JWT tokens carry auth).
```

### REST Response Example

```
GET /users/123

Response (200 OK):
{
    "id": 123,
    "name": "Alice",
    "email": "alice@email.com",
    "created_at": "2024-01-15T10:30:00Z"
}
```

### Key Principles

| Principle | What It Means |
|-----------|--------------|
| **Stateless** | Each request is independent. Server stores no session state. |
| **Client-Server** | Client and server evolve independently. |
| **Cacheable** | Responses declare if they're cacheable (ETag, Cache-Control). |
| **Uniform Interface** | Same patterns everywhere (URLs + HTTP verbs). |
| **Layered** | Client doesn't know if it's talking to server or intermediary (CDN/LB). |

### REST Status Codes

```
2xx Success:
  200 OK          → Request succeeded
  201 Created     → Resource created
  204 No Content  → Success, nothing to return

3xx Redirection:
  301 Moved       → Resource URL changed permanently
  304 Not Modified→ Cache is still valid

4xx Client Error:
  400 Bad Request → Invalid input
  401 Unauthorized→ Not logged in
  403 Forbidden   → Logged in but no permission
  404 Not Found   → Resource doesn't exist
  429 Too Many    → Rate limited

5xx Server Error:
  500 Internal    → Server crashed
  502 Bad Gateway → Upstream service down
  503 Unavailable → Server overloaded
```

### When to Use REST

| ✅ Use REST | ❌ Don't Use REST |
|------------|------------------|
| Public APIs | Real-time bidirectional communication |
| Simple CRUD apps | When you need to fetch nested data efficiently |
| Mobile/web apps | When bandwidth is extremely constrained |
| When standardization matters | Service-to-service internal communication (use gRPC) |

### Companies Using REST

Almost every company uses REST for external APIs. **Twitter, Stripe, GitHub, Amazon, Flipkart, Razorpay** — all expose REST APIs.

---

<a id="graphql"></a>
## GraphQL — Client-Driven Query Language

### What It Is (Analogy)

REST is like ordering a **set meal** — you get what the restaurant decides. GraphQL is like a **buffet** — you pick exactly what you want on your plate. Nothing more, nothing less.

### The Problem GraphQL Solves

```
REST Over-fetching:
  Mobile app shows user's name and avatar.
  GET /users/123
  → Returns: id, name, email, phone, address, bio, created_at,
            updated_at, preferences, settings, 20 other fields...

  Mobile downloaded 5KB of data but only needed 200 bytes.
  Wasteful on mobile networks.

REST Under-fetching:
  Mobile app shows user's profile + their recent orders.
  Step 1: GET /users/123       → user data
  Step 2: GET /users/123/orders → orders data
  → Two round trips. Slower.

GraphQL Solution:
  POST /graphql
  query {
    user(id: 123) {
      name
      avatar
      orders(last: 5) {
        id
        total
        status
      }
    }
  }

  → ONE request, exactly the fields needed, nested data included.
  → Returns: {"name":"Alice", "avatar":"url", "orders":[...]}
  → No over-fetching. No under-fetching.
```

### How It Works

```
GraphQL Architecture:

  Client                    GraphQL Server
  ┌────────┐                ┌──────────────────┐
  │ App    │──query──────►│ GraphQL Endpoint  │
  │        │              │                  │
  │        │              │  1. Parse query  │
  │        │              │  2. Validate     │
  │        │              │  3. Resolve      │
  │        │              │     fields       │
  │        │◄──response───│  4. Return       │
  └────────┘                │                  │
                            │  Resolvers call: │
                            │  • UserService   │
                            │  • OrderService  │
                            │  • ProductService│
                            └──────────────────┘
```

### GraphQL Schema Example

```graphql
type User {
    id: ID!
    name: String!
    email: String!
    avatar: String
    orders: [Order!]!
}

type Order {
    id: ID!
    total: Float!
    status: OrderStatus!
    items: [OrderItem!]!
}

type Query {
    user(id: ID!): User
    products(category: String): [Product!]!
}

type Mutation {
    createUser(name: String!, email: String!): User!
    placeOrder(items: [OrderItemInput!]!): Order!
}

type Subscription {
    orderUpdated(id: ID!): Order
}
```

### When to Use GraphQL

| ✅ Use GraphQL | ❌ Don't Use GraphQL |
|----------------|---------------------|
| Mobile apps (save bandwidth) | Simple CRUD (REST is simpler) |
| Complex nested data needs | Caching is harder (POST endpoint) |
| Multiple clients (web, mobile, TV) | File uploads (more complex than REST) |
| Rapid iteration on frontend | Real-time bidirectional (use WebSocket) |
| Aggregating data from multiple services | When you need HTTP caching |

### Companies Using GraphQL

| Company | Why |
|---------|-----|
| **Facebook** | Created GraphQL for mobile app |
| **Instagram** | Feed, profile data |
| **GitHub** | v4 API is GraphQL |
| **Airbnb** | API between microservices |
| **Netflix** | Netflix GraphQL Federation |
| **Twitter** | Mobile API |
| **Pinterest** | Pin/feed data |

---

<a id="grpc"></a>
## gRPC — High-Performance Remote Procedure Call

### What It Is (Analogy)

REST is like **sending a letter** — you write it, put it in an envelope, mail it, and wait for a reply. gRPC is like a **direct phone call** — you pick up, say "getUser(123)", and get an immediate response. No envelope, no address formatting — just direct function calls across machines.

### How It Works

```
TRADITIONAL REST:
  Client ──HTTP/1.1──► Server
  JSON: {"name": "Alice", "age": 30, "email": "alice@email.com"}
  → JSON is human-readable but verbose (text-based)
  → HTTP/1.1 has overhead (one request per connection)
  → Parsing JSON is slow

gRPC:
  Client ──HTTP/2──► Server
  Protobuf: [binary: 08 01 12 05 416c696365]
  → Binary format (compact, machine-readable)
  → HTTP/2 multiplexing (many requests per connection)
  → No parsing — deserialize directly
  → 5-10x faster than REST + JSON
```

### gRPC Streaming Modes

```
1. UNARY (like REST):
   Client ──request──► Server
   Client ◄─response─── Server

2. SERVER STREAMING:
   Client ──request──► Server
   Client ◄─response1── Server
   Client ◄─response2── Server
   Client ◄─response3── Server
   (e.g., live stock prices)

3. CLIENT STREAMING:
   Client ──data1──► Server
   Client ──data2──► Server
   Client ──data3──► Server
   Client ◄─response── Server
   (e.g., uploading metrics)

4. BIDIRECTIONAL STREAMING:
   Client ◄──► Server
   Client ◄──► Server
   Client ◄──► Server
   (both sides stream independently)
```

### When to Use gRPC

| ✅ Use gRPC | ❌ Don't Use gRPC |
|------------|-------------------|
| Service-to-service communication | Browser clients (limited browser support) |
| Low-latency internal APIs | Public APIs (REST is more universal) |
| Streaming data between services | Simple CRUD apps |
| Microservices architecture | When debugging raw responses (binary) |

### Companies Using gRPC

| Company | How |
|---------|-----|
| **Google** | Created gRPC. Used across all internal services |
| **Netflix** | Service-to-service communication |
| **Uber** | Internal microservice communication |
| **Square** | Payment processing between services |
| **Slack** | Real-time messaging backend |

---

<a id="protobuf"></a>
## Protocol Buffers — Serialization Format

### What It Is (Analogy)

JSON is like writing in **plain English** — everyone can read it, but it takes up space. Protocol Buffers (Protobuf) is like **shorthand notation** — compact, precise, and only machines need to understand it.

### JSON vs Protobuf Size Comparison

```
JSON representation:
{
  "user_id": 12345,
  "name": "Alice",
  "email": "alice@email.com",
  "active": true
}
→ ~100 bytes (text)

Protobuf representation:
12 08 41 6c 69 63 65 22 11 61 6c 69 63 65 40 ...
→ ~30 bytes (binary)

Protobuf is 3-10x smaller and 10-100x faster to serialize/deserialize.
```

### Protobuf Schema

```protobuf
syntax = "proto3";

message User {
    int32 user_id = 1;
    string name = 2;
    string email = 3;
    bool active = 4;
    repeated string roles = 5;
}

service UserService {
    rpc GetUser(GetUserRequest) returns (User);
    rpc CreateUser(User) returns (User);
}
```

---

<a id="websocket"></a>
## WebSocket — Bidirectional Real-Time Communication

### What It Is (Analogy)

REST is like **sending text messages** — each message is independent, with overhead per message. WebSocket is like a **phone call** — once connected, both parties can talk freely in both directions without hanging up.

### How It Works (Connection Lifecycle)

```
Step 1: HTTP Upgrade Request (starts as HTTP)

  Client ──► Server
  GET /ws HTTP/1.1
  Upgrade: websocket
  Connection: Upgrade
  Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==

Step 2: Server Accepts Upgrade

  Server ──► Client
  HTTP/1.1 101 Switching Protocols
  Upgrade: websocket
  Connection: Upgrade
  Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=

Step 3: Connection Upgraded — Now bidirectional TCP

  Client ◄──────► Server
  (Both can send messages anytime, no request/response pattern)
  (Stays open until either side closes it)

Step 4: Heartbeat (keep-alive)

  Client ──ping──► Server
  Client ◄─pong─── Server
  (Every 30 seconds to keep connection alive through proxies)
```

### WebSocket vs HTTP

```
HTTP (Request-Response):
  Client ──request──► Server     Each request needs:
  Client ◄─response── Server     - New TCP connection (or pooled)
  Client ──request──► Server     - HTTP headers (~800 bytes overhead)
  Client ◄─response── Server     - Latency per request

WebSocket (Persistent):
  Client ◄──► Server             One connection stays open
  Client ◄──► Server             Each message ~2-10 bytes overhead
  Client ◄──► Server             Near-zero latency after connect
  Client ◄──► Server             Server can PUSH data anytime
```

### When to Use WebSocket

| ✅ Use WebSocket | ❌ Don't Use WebSocket |
|-----------------|----------------------|
| Chat apps (WhatsApp, Slack) | Simple request-response |
| Real-time gaming | One-way notifications (use SSE) |
| Live collaboration (Google Docs) | REST CRUD operations |
| Financial tickers (bidirectional) | Infrequent data updates |
| IoT device control | |

### Companies Using WebSocket

| Company | How |
|---------|-----|
| **WhatsApp** | Message delivery (via custom protocol on top of WebSocket) |
| **Slack** | Real-time messaging, typing indicators |
| **Zomato** | Live order tracking (rider location updates) |
| **Robinhood** | Real-time stock prices |
| **Google Docs** | Collaborative editing |

---

<a id="sse"></a>
## Server-Sent Events (SSE) — One-Way Streaming

### What It Is (Analogy)

If WebSocket is a phone call (both sides talk), SSE is like a **radio broadcast** — the server talks, the client listens. Simple, efficient, one-directional.

### How It Works

```
Client ──GET /events──► Server
Client ◄─data: {"msg":"hello"}── Server
Client ◄─data: {"msg":"world"}── Server
Client ◄─data: {"msg":"again"}── Server
  (Connection stays open, server keeps pushing)

The Server pushes events:
  Content-Type: text/event-stream
  data: {"score": 42}\n\n
  data: {"score": 43}\n\n
  data: {"score": 44}\n\n

Client uses EventSource API:
  const events = new EventSource('/events');
  events.onmessage = (e) => {
      console.log(JSON.parse(e.data));
  };
```

### SSE vs WebSocket

| Feature | SSE | WebSocket |
|---------|-----|-----------|
| **Direction** | Server → Client only | Bidirectional |
| **Protocol** | HTTP (standard) | Custom protocol over TCP |
| **Reconnection** | Auto-reconnect built-in | Must implement manually |
| **Browser support** | EventSource API | WebSocket API |
| **Proxy friendly** | Yes (standard HTTP) | Often blocked by corporate proxies |
| **Max connections** | 6 per domain (HTTP/1.1) | Unlimited |
| **Best for** | Notifications, live scores | Chat, gaming |

### When to Use SSE

| ✅ Use SSE | ❌ Don't Use SSE |
|-----------|-----------------|
| Live notifications | Chat (client needs to send too) |
| Real-time score updates | Gaming |
| Social media feeds (push) | Collaborative editing |
| Stock ticker (display only) | IoT device control |

### Companies Using SSE

| Company | How |
|---------|-----|
| **Twitter** | Real-time tweet notifications |
| **Facebook** | Live notification updates |
| **Flipkart** | Order status updates during Big Billion Days |

---

<a id="long-polling"></a>
## Long Polling — The Legacy Fallback

### What It Is (Analong first)

Long polling is like calling a store and saying "I'll hold" until a delivery arrives, instead of calling back every 5 minutes to check.

### How It Works

```
NORMAL POLLING (Inefficient):
  Client: "Any new messages?"     → Server: "No"
  (wait 5 sec)
  Client: "Any new messages?"     → Server: "No"
  (wait 5 sec)
  Client: "Any new messages?"     → Server: "Yes! Here they are"
  → Wasteful. Lots of empty requests.

LONG POLLING (Better):
  Client: "Any new messages?"     → Server holds request open...
  (server waits 30 seconds)        → Server: "Yes! Here they are"
  Client: "Any new messages?"     → Server holds request open...
  → One request per actual message. Much more efficient.

  Client ──request──► Server (hold)
  (wait... wait... 25 seconds later)
  Client ◄─response─── Server (event happened!)

  Client immediately sends next request:
  Client ──request──► Server (hold)
```

### When to Use Long Polling

| ✅ Use Long Polling | ❌ Don't Use Long Polling |
|--------------------|--------------------------|
| WebSocket not available | If WebSocket is available |
| Corporate proxies blocking WebSocket | New applications |
| Legacy browser support | Performance-critical apps |

### Companies Using Long Polling

| Company | How |
|---------|-----|
| **Facebook** (early) | Chat before WebSocket was widely supported |
| **Google** (early) | Gmail chat used long polling |

---

<a id="comparison"></a>
## Master Comparison Table

| API Style | Direction | Protocol | Latency | Use Case | Used By |
|-----------|----------|----------|---------|----------|---------|
| **REST** | Request-response | HTTP/1.1 | ~50-200ms | CRUD, public APIs | Everyone |
| **GraphQL** | Request-response | HTTP | ~50-200ms | Nested data, mobile | Facebook, GitHub |
| **gRPC** | Request-response + streaming | HTTP/2 | ~1-10ms | Service-to-service | Google, Netflix |
| **WebSocket** | Bidirectional | TCP (custom) | <5ms after connect | Chat, gaming | WhatsApp, Slack |
| **SSE** | Server→Client | HTTP | <5ms after connect | Notifications | Twitter, Facebook |
| **Long Polling** | Server→Client | HTTP | Variable (0-30s) | Legacy fallback | (legacy) |
| **WebRTC** | Peer-to-peer | UDP/SRTP | <50ms | Video/audio calls | Zoom, Google Meet |

---

## How to Choose — Decision Tree

```
START HERE
  │
  ├── Is it real-time (data must arrive within seconds)?
  │   │
  │   ├── YES, bidirectional (chat, gaming)?
  │   │   └── WebSocket
  │   │
  │   ├── YES, one-way (notifications, live scores)?
  │   │   └── SSE
  │   │
  │   └── YES, video/audio?
  │       └── WebRTC
  │
  ├── Is it service-to-service (internal)?
  │   └── gRPC (fast, typed, streaming)
  │
  ├── Client needs specific fields from multiple resources?
  │   └── GraphQL
  │
  └── Standard CRUD / public API?
      └── REST
```

## The Evolution of API Communication

```
1990s:  XML-RPC / SOAP     (heavy, verbose, enterprise)
2000s:  REST + JSON         (simple, stateless, universal)
2010s:  GraphQL             (client-driven, efficient)
        gRPC                (fast, typed, internal)
2020s:  HTTP/3 + QUIC       (multiplexed, low-latency)
        WebTransport        (WebSocket-like over HTTP/3)
```

Each evolution solved problems of the previous one. But REST is still the most widely used and will remain so for public APIs.

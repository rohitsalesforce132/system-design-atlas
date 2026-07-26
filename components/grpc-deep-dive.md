# gRPC & Protocol Buffers — The Complete Deep Dive

> gRPC is mentioned 64 times across this atlas. Google created it, Netflix and Uber run their entire microservice backends on it. This guide covers how gRPC works internally, why it's 10x faster than REST, and how Protobuf makes it possible.

---

## Table of Contents

1. [What Problem gRPC Solves](#the-problem)
2. [REST + JSON vs gRPC + Protobuf — Head to Head](#rest-vs-grpc)
3. [Protocol Buffers — How Binary Serialization Works](#protobuf)
4. [gRPC Architecture — Channel, Stub, Server](#architecture)
5. [The Four RPC Types](#rpc-types)
6. [HTTP/2 — The Transport Layer](#http2)
7. [How gRPC Achieves Low Latency](#latency)
8. [Error Handling — Status Codes](#errors)
9. [Deadlines, Timeouts, and Cancellation](#deadlines)
10. [Interceptors — Middleware for gRPC](#interceptors)
11. [Load Balancing gRPC](#lb)
12. [gRPC vs GraphQL vs REST — Decision Matrix](#decision)
13. [How Real Companies Use gRPC](#real-apps)
14. [How YOU Can Build This](#build)

---

<a id="the-problem"></a>
## What Problem gRPC Solves

### The REST Performance Tax

```
Every REST API call pays these costs:

  1. JSON SERIALIZATION (expensive):
     Request:  { "user_id": 12345, "name": "Alice", "email": "alice@email.com" }
     → JSON is text. Text is verbose. Parsing text is slow.
     → JSON serialization: ~5000 ns per field
     → For a 100-field object: 500,000 ns = 0.5ms just for parsing

  2. HTTP HEADERS (verbose):
     POST /api/users HTTP/1.1\r\n
     Host: api.example.com\r\n
     Content-Type: application/json\r\n
     Authorization: Bearer eyJhbGc...\r\n
     User-Agent: MyApp/1.0\r\n
     Accept: application/json\r\n
     \r\n
     → ~300-800 bytes of headers per request
     → Parsed character-by-character

  3. HTTP/1.1 CONNECTIONS (one at a time):
     → HTTP/1.1 allows one request per TCP connection at a time
     → Or use connection pooling (6-10 connections per host)
     → Still: each request = full round trip

  4. NO TYPE SAFETY:
     → Client sends JSON → server parses → maybe field is missing
     → No compile-time checking
     → Runtime errors from type mismatches
```

### What gRPC Does Differently

```
gRPC replaces:

  REST API (HTTP/1.1 + JSON)
    with
  gRPC (HTTP/2 + Protobuf)

  ┌──────────────────────────────────────────────────────┐
  │  REST + JSON                  gRPC + Protobuf         │
  │                                                       │
  │  Text serialization           Binary serialization    │
  │  (~800 bytes/msg)             (~30 bytes/msg)         │
  │                                                       │
  │  HTTP/1.1                     HTTP/2                  │
  │  (one request/conn)           (1000s per connection)  │
  │                                                       │
  │  No type safety               Compiled stubs          │
  │  (parse at runtime)           (compile-time checking) │
  │                                                       │
  │  Request-response only        4 streaming modes       │
  │                                                       │
  │  ~50-200ms latency            ~1-10ms latency         │
  └──────────────────────────────────────────────────────┘
```

---

<a id="rest-vs-grpc"></a>
## REST + JSON vs gRPC + Protobuf — Head to Head

### Size Comparison

```
Same data: { user_id: 12345, name: "Alice", email: "alice@email.com", active: true }

JSON (text):
{
  "user_id": 12345,
  "name": "Alice",
  "email": "alice@email.com",
  "active": true
}
→ ~100 bytes (text, includes field names, braces, quotes, colons)

Protobuf (binary):
08 B9 60 12 05 41 6C 69 63 65 1A 10 61 6C 69 63 65 40 65 6D 61 69 6C 2E 63 6F 6D 28 01
→ ~30 bytes (binary, field numbers instead of names, no delimiters)

→ Protobuf is 3-10x smaller
→ For large messages with many fields: 10-100x smaller
```

### Speed Comparison

```
Operation              JSON              Protobuf
──────────────────────────────────────────────────
Serialize 1KB object   50μs              5μs        (10x faster)
Deserialize 1KB object 80μs              8μs        (10x faster)
Serialize 100KB        5ms               0.5ms      (10x faster)
Network transfer       More bytes        Fewer bytes (3x less)

For 10,000 calls/sec:
  JSON:    10,000 × 130μs = 1.3 seconds of CPU per second
  Protobuf: 10,000 × 13μs = 0.13 seconds of CPU per second
  → 10x less CPU for serialization alone
```

### Feature Comparison

| Feature | REST + JSON | gRPC + Protobuf |
|---------|-------------|-----------------|
| **Serialization** | JSON (text) | Protobuf (binary) |
| **Transport** | HTTP/1.1 (usually) | HTTP/2 (always) |
| **Type safety** | None (runtime parsing) | Compiled stubs (compile-time) |
| **Streaming** | No (request-response) | Yes (4 modes) |
| **Browser support** | Universal | Limited (requires gRPC-Web proxy) |
| **Code generation** | Manual (OpenAPI optional) | Automatic (from .proto) |
| **Bidirectional** | No | Yes |
| **Multiplexing** | No (HTTP/1.1) | Yes (HTTP/2) |
| **Best for** | External/public APIs | Internal service-to-service |

---

<a id="protobuf"></a>
## Protocol Buffers — How Binary Serialization Works

### What Is Protobuf?

**Analogy:** JSON is like sending a letter in **plain English** — anyone can read it, but it takes space. Protobuf is like sending a message in **coded shorthand** — compact, precise, and only machines need to decode it.

### The .proto File (Schema Definition)

```protobuf
syntax = "proto3";  // Protocol Buffers version 3

message User {
    int32  user_id = 1;   // Field number 1, type int32
    string name    = 2;   // Field number 2, type string
    string email   = 3;   // Field number 3, type string
    bool   active  = 4;   // Field number 4, type bool
    repeated string roles = 5;  // Field 5, repeated (like an array)
}

message GetUserRequest {
    int32 user_id = 1;
}

message GetUserResponse {
    User user = 1;
}

service UserService {
    rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
```

### Key Concept: Field Numbers (Not Field Names)

```
JSON stores field NAMES:
  {"user_id": 12345, "name": "Alice"}
  → "user_id" takes 10 bytes (including quotes and colon)
  → "name" takes 6 bytes

Protobuf stores field NUMBERS:
  Field 1 (user_id): value 12345
  Field 2 (name):    value "Alice"
  → Field number encoded as 1 byte
  → No field names in the wire format

  This is why Protobuf is so compact.
  Field numbers are defined in the .proto file (schema).
  Both sender and receiver have the schema → they know
  what field number maps to what field name.
```

### Wire Format — How Each Field Is Encoded

```
Each field on the wire: [key byte(s)] [value byte(s)]

Key = (field_number << 3) | wire_type

Wire types:
  0: Varint (int32, int64, bool, enum)
  1: 64-bit (fixed64, double)
  2: Length-delimited (string, bytes, repeated, embedded message)
  5: 32-bit (fixed32, float)

Example: Encode user_id=12345 (field 1, varint)

  Key = (1 << 3) | 0 = 0x08
  Value = 12345 in varint encoding

  Varint encoding of 12345:
    12345 in binary = 11000000111001
    → Split into 7-bit groups (little-endian):
      Group 1: 1110010 (bits 0-6)
      Group 2: 1100000 (bits 7-13)

    → Add continuation bits (MSB):
      Byte 1: 1_1110010 = 0xF2  (MSB=1 → more bytes follow)
      Byte 2: 0_1100000 = 0x60  (MSB=0 → last byte)

  Full encoding: [0x08] [0xF2] [0x60]
  → 3 bytes for field number + int32 value of 12345

  JSON: "user_id":12345, = 15 bytes
  Protobuf: 3 bytes (5x smaller)
```

### Varint Encoding — Small Numbers Use Fewer Bytes

```
Protobuf is smart about integers:

  Value 1:   1 byte (small number = short varint)
  Value 300: 2 bytes
  Value 12345: 3 bytes
  Value 1000000: 3 bytes
  Value 2^32: 5 bytes

  Small values use fewer bytes. JSON always uses the full decimal representation:
  "id": 1 → 4 characters
  "id": 1000000 → 11 characters
```

### Backward and Forward Compatibility

```
Protobuf is designed for schema evolution:

  Version 1 (.proto):
  message User {
    int32 user_id = 1;
    string name = 2;
  }

  Version 2 (add new field):
  message User {
    int32 user_id = 1;
    string name = 2;
    string email = 3;  ← NEW FIELD
  }

  Version 3 (remove field, but DON'T reuse the number):
  message User {
    int32 user_id = 1;
    string email = 3;
    // Field 2 removed (reserved)
    reserved 2;  ← Prevents reuse of field number 2
    reserved "name";
  }

  COMPATIBILITY RULES:
  → Adding a new field: Old clients ignore it. ✓
  → Removing a field: New clients don't find it. ✓
  → NEVER change a field number (breaks compatibility)
  → NEVER change a field type (breaks parsing)
  → NEVER reuse a field number (confusing)

  This is CRITICAL for microservices:
  → Service A sends v2 message (with email field)
  → Service B still uses v1 parser
  → Service B ignores field 3 (email) → works!
  → No breaking change → no coordinated deployment needed
```

### Code Generation

```
From .proto file, generate code for any language:

  protoc --go_out=. user.proto        → Go structs + gRPC stub
  protoc --python_out=. user.proto    → Python classes + gRPC stub
  protoc --java_out=. user.proto      → Java classes + gRPC stub
  protoc --js_out=. user.proto        → JavaScript classes

  Generated code includes:
  → Message classes (User, GetUserRequest, etc.)
  → Serialization/deserialization functions
  → gRPC client stub (UserService.GetUser())
  → gRPC server interface (UserService interface to implement)

  → No manual serialization code.
  → Type-safe at compile time.
  → Cross-language (Go client talks to Python server seamlessly).
```

---

<a id="architecture"></a>
## gRPC Architecture — Channel, Stub, Server

```
┌──────────────────────────────────────────────────────────┐
│  CLIENT                                                    │
│                                                           │
│  ┌───────────────┐                                        │
│  │  gRPC Stub     │  ← Generated from .proto               │
│  │  (UserService) │    Type-safe function calls            │
│  │                │    user = stub.GetUser(request)        │
│  └───────┬───────┘                                        │
│          │                                                │
│  ┌───────▼───────┐                                        │
│  │  Channel        │  ← Virtual connection to server        │
│  │  (HTTP/2 conn) │    Pooled, reused across calls         │
│  │                 │    One channel = many concurrent RPCs  │
│  └───────┬───────┘                                        │
│          │                                                │
└──────────┼────────────────────────────────────────────────┘
           │
           │  HTTP/2 + Protobuf over TCP
           │
┌──────────┼────────────────────────────────────────────────┐
│  SERVER │                                                    │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  HTTP/2 Server│                                           │
│  │  (listener)   │                                           │
│  └───────┬───────┘                                           │
│          │                                                   │
│  ┌───────▼───────┐                                           │
│  │  gRPC Server   │  ← Routes RPC to correct handler         │
│  │  (dispatcher)  │                                           │
│  └───────┬───────┘                                           │
│          │                                                   │
│  ┌───────▼───────┐                                           │
│  │  Service Impl  │  ← Your code                              │
│  │  (UserService) │    class UserServiceImpl:                 │
│  │                 │      def GetUser(self, request):         │
│  │                 │        return GetUserResponse(...)       │
│  └───────────────┘                                           │
└──────────────────────────────────────────────────────────┘
```

### Channel — The Smart Connection

```
  A gRPC Channel is like a HTTP connection pool, but smarter:

  ┌───────────────────────────────────────────────────────┐
  │  CHANNEL (one per server)                              │
  │                                                       │
  │  ┌─────────┐ ┌─────────┐ ┌─────────┐                │
  │  │HTTP/2   │ │HTTP/2   │ │HTTP/2   │                │
  │  │Conn 1   │ │Conn 2   │ │Conn 3   │                │
  │  │         │ │         │ │         │                │
  │  │RPC A    │ │RPC D    │ │RPC F    │                │
  │  │RPC B    │ │RPC E    │ │RPC G    │                │
  │  │RPC C    │ │         │ │RPC H    │                │
  │  └─────────┘ └─────────┘ └─────────┘                │
  │                                                       │
  │  Multiplexing: Multiple RPCs on one HTTP/2 connection │
  │  Load balancing: New RPC → pick least-loaded conn     │
  │  Reuse: Connection stays open across calls            │
  │  Health: Dead connections are replaced automatically  │
  └───────────────────────────────────────────────────────┘
```

---

<a id="rpc-types"></a>
## The Four RPC Types

### 1. Unary (Request → Response) — Like REST

```protobuf
service UserService {
    rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
```

```
Client ──request──► Server
Client ◄─response─── Server

  Most similar to REST. One request, one response.
  Use for: Simple lookups, mutations, API calls.
```

### 2. Server Streaming (Request → Stream of Responses)

```protobuf
service OrderService {
    rpc TrackOrder(OrderId) returns (stream OrderUpdate);
}
```

```
Client ──request──► Server
Client ◄─update 1─── Server
Client ◄─update 2─── Server
Client ◄─update 3─── Server
Client ◄─complete─── Server

  Server sends multiple responses over time.
  Client reads until server closes the stream.
  Use for: Live order tracking, real-time updates, log streaming, large data pagination.
```

### 3. Client Streaming (Stream of Requests → Response)

```protobuf
service AnalyticsService {
    rpc UploadMetrics(stream Metric) returns (UploadSummary);
}
```

```
Client ──metric 1──► Server
Client ──metric 2──► Server
Client ──metric 3──► Server
Client ──done─────► Server
Client ◄─summary─── Server

  Client sends multiple requests. Server responds once.
  Use for: Batch uploads, streaming metrics, file uploads in chunks.
```

### 4. Bidirectional Streaming (Both Stream Simultaneously)

```protobuf
service ChatService {
    rpc Chat(stream ChatMessage) returns (stream ChatMessage);
}
```

```
Client ──message──► Server
Client ◄─message─── Server
Client ──message──► Server
Client ◄─message─── Server
  (both sides send independently, in any order)

  Full-duplex streaming.
  Use for: Chat, collaborative editing, real-time gaming, control panels.
```

---

<a id="http2"></a>
## HTTP/2 — The Transport Layer

gRPC requires HTTP/2. Here's why:

### HTTP/1.1 vs HTTP/2

```
HTTP/1.1:
  One request per TCP connection at a time.

  Client ──request 1──► Server
  Client ◄─response 1── (wait...)
  Client ──request 2──► Server
  Client ◄─response 2── (wait...)

  To parallelize: open 6 connections (browser limit).
  But each connection has TCP + TLS overhead.

HTTP/2:
  Multiple requests (streams) on ONE TCP connection.

  Client ──stream 1: request──► Server
  Client ──stream 3: request──► Server  (concurrent!)
  Client ──stream 5: request──► Server  (concurrent!)
  Client ◄─stream 3: response── Server  (responses can interleave!)
  Client ◄─stream 1: response── Server
  Client ◄─stream 5: response── Server

  ONE connection handles THOUSANDS of concurrent streams.
```

### HTTP/2 Features That gRPC Uses

```
1. MULTIPLEXING:
   → Multiple streams on one connection
   → No head-of-line blocking (stream 2 doesn't wait for stream 1)

2. HEADER COMPRESSION (HPACK):
   → HTTP/1.1 sends full headers every request (~800 bytes)
   → HTTP/2 compresses headers with HPACK
   → Repeated headers (Host, Content-Type, Authorization) sent once
   → Subsequent requests: just the diff (~20 bytes)

3. BINARY FRAMING:
   → HTTP/1.1 is text-based (human-readable, slow to parse)
   → HTTP/2 is binary (machine-readable, fast to parse)
   → Frame: [length: 3 bytes] [type: 1 byte] [flags: 1 byte] [stream_id: 4 bytes] [payload]

4. FLOW CONTROL:
   → Per-stream flow control (like TCP but per-stream)
   → Prevents fast sender from overwhelming slow receiver

5. SERVER PUSH (not used by gRPC):
   → Server can push resources proactively (HTTP/2 feature, but gRPC doesn't use it)
```

---

<a id="latency"></a>
## How gRPC Achieves Low Latency

```
gRPC's latency advantage comes from 4 sources:

1. PROTOBUF (vs JSON):
   → 10x faster serialization/deserialization
   → 3-10x smaller payloads → less network transfer time

2. HTTP/2 MULTIPLEXING:
   → No connection setup per request (reuse HTTP/2 connection)
   → Multiple concurrent RPCs on one connection
   → No head-of-line blocking

3. HPACK HEADER COMPRESSION:
   → First request: full headers (~800 bytes)
   → Subsequent requests: delta only (~20 bytes)
   → gRPC adds its own metadata (~100 bytes first, ~10 bytes after)

4. BINARY FRAMING:
   → No text parsing (HTTP/1.1 parses text headers)
   → Direct memory access to frame data

TOTAL: gRPC is typically 5-10x faster than REST+JSON for
       equivalent operations.
```

---

<a id="errors"></a>
## Error Handling — Status Codes

gRPC uses its own status codes (not HTTP status codes):

```
Code                    Number  Meaning
──────────────────────────────────────────────────────
OK                      0       Success
CANCELLED               1       Client cancelled the call
UNKNOWN                 2       Server threw unexpected error
INVALID_ARGUMENT        3       Client sent bad parameters
DEADLINE_EXCEEDED       4       Timeout (deadline passed)
NOT_FOUND               5       Resource doesn't exist
ALREADY_EXISTS          6       Resource already created
PERMISSION_DENIED       7       No access
RESOURCE_EXHAUSTED      8       Quota exceeded / rate limited
FAILED_PRECONDITION     9       System not ready (e.g., DB down)
ABORTED                 10      Conflict (retry with backoff)
OUT_OF_RANGE            11      Value out of valid range
UNIMPLEMENTED           12      Method not implemented
INTERNAL                13      Server bug
UNAVAILABLE             14      Service down (retry)
DATA_LOSS               15      Unrecoverable data loss
UNAUTHENTICATED         16      No valid authentication
```

### Rich Error Model

```python
# Server returns rich error details:
import grpc
from google.rpc import error_details_pb2

def get_user(request):
    user = db.find_user(request.user_id)
    if not user:
        raise grpc.RpcError(
            grpc.Status(
                code=grpc.StatusCode.NOT_FOUND,
                details="User not found",
                metadata=[
                    ("error_code", "USER_NOT_FOUND"),
                    ("retry_after", "60"),
                ]
            )
        )

# Client handles error:
try:
    user = stub.GetUser(request)
except grpc.RpcError as e:
    if e.code() == grpc.StatusCode.NOT_FOUND:
        print(f"User {request.user_id} doesn't exist")
    elif e.code() == grpc.StatusCode.UNAVAILABLE:
        print("Server down, retrying...")
        time.sleep(1)
        retry()
```

---

<a id="deadlines"></a>
## Deadlines, Timeouts, and Cancellation

### Why Deadlines Matter

```
WITHOUT deadlines:
  Client calls Server A → Server A calls Server B → Server B calls DB
  → DB is slow (10 seconds)
  → Server B waits
  → Server A waits
  → Client waits
  → Client gives up after 30 seconds → closes connection
  → But Server A and Server B are STILL processing (wasted work)
  → If 1000 clients do this → cascade of wasted work → system overload

WITH deadlines:
  Client sets deadline: "I need a response in 2 seconds"
  → Deadline propagates through Server A → Server B → DB
  → If DB can't respond in time → all servers cancel early
  → Resources freed immediately
```

### Deadline Propagation

```python
# Client sets deadline
response = stub.GetUser(
    request,
    timeout=2.0  # 2 second deadline
)

# Server receives deadline and propagates:
def GetUser(self, request, context):
    # context has the deadline
    time_remaining = context.time_remaining()
    # 1.8 seconds left

    # Call downstream service with SAME deadline
    user = user_db.get(
        request.user_id,
        timeout=time_remaining  # propagate!
    )

    # If 1.8s passes → context.cancelled = True
    # All downstream calls also cancelled
```

---

<a id="interceptors"></a>
## Interceptors — Middleware for gRPC

```
Interceptors are like Express middleware or Django middleware:

  Client ──► [Interceptor 1] ──► [Interceptor 2] ──► [gRPC Call] ──► Server
             (logging)            (auth)               (actual RPC)

  Server ──► [Interceptor 1] ──► [Interceptor 2] ──► [Handler] ──► Response
             (auth check)        (metrics)             (your code)
```

### Common Interceptor Use Cases

```python
# Server-side interceptors

# 1. AUTHENTICATION INTERCEPTOR
def auth_interceptor(handler, request, context):
    token = context.invocation_metadata().get('authorization')
    if not validate_jwt(token):
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
    return handler(request)  # proceed to actual handler

# 2. LOGGING INTERCEPTOR
def logging_interceptor(handler, request, context):
    start = time.time()
    method = context.method_name
    try:
        response = handler(request)
        latency = time.time() - start
        log.info(f"{method} OK in {latency:.3f}s")
        return response
    except Exception as e:
        latency = time.time() - start
        log.error(f"{method} FAILED in {latency:.3f}s: {e}")
        raise

# 3. METRICS INTERCEPTOR
def metrics_interceptor(handler, request, context):
    start = time.time()
    try:
        response = handler(request)
        prometheus_histogram.observe(
            context.method_name,
            time.time() - start,
            'success'
        )
        return response
    except:
        prometheus_counter.inc(
            context.method_name,
            'error'
        )
        raise

# Register interceptors
server = grpc.Server(executor)
server.add_interceptor(auth_interceptor)
server.add_interceptor(logging_interceptor)
server.add_interceptor(metrics_interceptor)
```

---

<a id="lb"></a>
## Load Balancing gRPC

### The Problem: HTTP/2 Breaks Traditional LB

```
Traditional L4 load balancer with HTTP/1.1:
  Client opens connection → LB routes to Server 1
  Client opens connection → LB routes to Server 2
  → Each connection = one server. LB works.

With HTTP/2 (gRPC):
  Client opens ONE long-lived connection → LB routes to Server 1
  → ALL subsequent RPCs go to Server 1 (through the same connection)
  → Server 2, 3, 4 get NO traffic. LB fails.
```

### Solutions

```
SOLUTION 1: CLIENT-SIDE LOAD BALANCING
  Client knows about all servers. Picks one per RPC.

  Client → [Server 1, Server 2, Server 3]
  RPC 1 → Server 1 (round-robin)
  RPC 2 → Server 2
  RPC 3 → Server 3
  RPC 4 → Server 1

  → gRPC has built-in client-side LB (round-robin, pick-first, custom)
  → Requires service discovery (Consul, Kubernetes DNS, xDS)

SOLUTION 2: PROXY-BASED (Envoy)
  Client → Envoy Proxy → [Server 1, Server 2, Server 3]

  Envoy understands HTTP/2:
  → Receives RPCs on one connection
  → Distributes to backend servers
  → One Envoy per service (sidecar pattern)

SOLUTION 3: KUBERNETES SERVICE (DNS-based)
  gRPC client connects to "my-service.default.svc.cluster.local"
  → DNS resolves to multiple pod IPs
  → gRPC client-side LB rotates through IPs
  → Works but requires client keepalive and reconnection logic
```

---

<a id="decision"></a>
## gRPC vs GraphQL vs REST — Decision Matrix

```
When to use what?

  External/public API (consumed by browsers, mobile)?
  → REST (universal, easy to consume, cacheable)

  Internal service-to-service (microservices)?
  → gRPC (fast, typed, streaming)

  Mobile client needs specific fields from multiple resources?
  → GraphQL (client-driven, no over/under-fetching)

  Real-time bidirectional communication?
  → gRPC bidirectional streaming (for services)
  → WebSocket (for browser clients)

  Simple CRUD app?
  → REST (simplest, well-understood)

  High-throughput data pipeline?
  → gRPC streaming (upload metrics, download large datasets)
```

---

<a id="real-apps"></a>
## How Real Companies Use gRPC

| Company | How | Scale |
|---------|-----|-------|
| **Google** | Created gRPC. All internal services use it. | Millions of RPCs/sec |
| **Netflix** | Service-to-service communication | 500+ microservices |
| **Uber** | Internal microservice communication | 2,200+ services |
| **Square** | Payment processing between services | Thousands of RPCs/sec |
| **Slack** | Real-time messaging backend | Millions of messages |
| **Dropbox** | Internal service communication | Hundreds of services |
| **Cisco** | Network device management | Enterprise scale |
| **CoreOS** | etcd (distributed key-value store) | Critical infrastructure |

### Google's Internal Usage

```
Google uses gRPC (and its predecessor Stubby) for everything:

  Search frontend → Search backend:     gRPC
  Ads service → Bidding service:        gRPC
  YouTube frontend → Video service:     gRPC
  Gmail frontend → Mail storage:        gRPC

  Billions of RPCs per second inside Google.
  Every Google product is built on gRPC-style communication.
```

---

<a id="build"></a>
## How YOU Can Build This

### Define Your API (.proto file)

```protobuf
// user.proto
syntax = "proto3";

package myapp;

service UserService {
    rpc GetUser(GetUserRequest) returns (User);
    rpc ListUsers(ListUsersRequest) returns (stream User);
    rpc CreateUser(CreateUserRequest) returns (User);
}

message User {
    int32 id = 1;
    string name = 2;
    string email = 3;
}

message GetUserRequest {
    int32 id = 1;
}

message ListUsersRequest {
    int32 limit = 1;
}

message CreateUserRequest {
    string name = 1;
    string email = 2;
}
```

### Generate Code

```bash
# Python
python -m grpc_tools.protoc \
    --python_out=. \
    --grpc_python_out=. \
    user.proto

# Go
protoc --go_out=. --go-grpc_out=. user.proto

# Java
protoc --java_out=. --grpc-java_out=. user.proto
```

### Implement the Server (Python)

```python
import grpc
from concurrent import futures
import user_pb2
import user_pb2_grpc

class UserServiceImpl(user_pb2_grpc.UserServiceServicer):
    def GetUser(self, request, context):
        # Your business logic
        user = db.find_user(request.id)
        if not user:
            context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
        return user_pb2.User(id=user.id, name=user.name, email=user.email)

    def ListUsers(self, request, context):
        # Server streaming
        for user in db.list_users(limit=request.limit):
            yield user_pb2.User(id=user.id, name=user.name, email=user.email)

    def CreateUser(self, request, context):
        user = db.create_user(name=request.name, email=request.email)
        return user_pb2.User(id=user.id, name=user.name, email=user.email)

# Start server
server = grpc.Server(futures.ThreadPoolExecutor(max_workers=10))
user_pb2_grpc.add_UserServiceServicer_to_server(UserServiceImpl(), server)
server.add_insecure_port('[::]:50051')
server.start()
server.wait_for_termination()
```

### Implement the Client (Python)

```python
import grpc
import user_pb2
import user_pb2_grpc

# Create channel (connection)
channel = grpc.insecure_channel('localhost:50051')

# Create stub (type-safe client)
stub = user_pb2_grpc.UserServiceStub(channel)

# Unary call
user = stub.GetUser(user_pb2.GetUserRequest(id=123))
print(f"User: {user.name} ({user.email})")

# Server streaming
for user in stub.ListUsers(user_pb2.ListUsersRequest(limit=10)):
    print(f"  - {user.name}")

# With deadline
try:
    user = stub.GetUser(
        user_pb2.GetUserRequest(id=123),
        timeout=2.0  # 2 second deadline
    )
except grpc.RpcError as e:
    if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        print("Timeout!")
    elif e.code() == grpc.StatusCode.NOT_FOUND:
        print("User not found!")
```

---

## Common Interview Questions

**Q: Why is gRPC faster than REST?**

A: Four reasons: (1) Protobuf binary serialization is 10x faster than JSON text parsing, (2) HTTP/2 multiplexing allows thousands of concurrent RPCs on one connection (no per-request connection overhead), (3) HPACK header compression sends only header deltas after the first request, (4) Binary framing avoids text parsing overhead. Combined: 5-10x lower latency than REST.

**Q: Explain Protocol Buffers and why field numbers matter.**

A: Protobuf encodes data as field-number + value pairs, not field-name + value. Field number 1 takes 1 byte. JSON's "user_id" takes 10 bytes. Both client and server have the .proto schema, so they map field numbers to names. Field numbers must NEVER change once deployed — they're the wire identity. New fields can be added (old clients ignore them) and fields can be removed (mark as reserved) without breaking compatibility. This is critical for microservices with independent deployment cycles.

**Q: What are the four gRPC RPC types?**

A: (1) Unary: one request, one response (like REST). (2) Server streaming: one request, multiple responses over time (live updates, pagination). (3) Client streaming: multiple requests, one response (batch upload, metrics). (4) Bidirectional streaming: both sides stream independently (chat, collaborative editing). The streaming types are powered by HTTP/2's multiplexed streams.

**Q: How do you load balance gRPC?**

A: Traditional L4 load balancers don't work because gRPC uses long-lived HTTP/2 connections — all traffic goes to the first server the connection hits. Solutions: (1) Client-side load balancing — the gRPC client knows about all backends and round-robins per RPC. (2) Proxy-based (Envoy) — Envoy terminates the HTTP/2 connection and distributes RPCs to backends. (3) In Kubernetes, use a headless service + client-side LB, or an Envoy sidecar.

**Q: How does deadline propagation work?**

A: The client sets a deadline (e.g., 2 seconds). gRPC encodes this deadline in the RPC metadata. When the server receives the call, it sees the remaining time. If the server calls downstream services, it passes the remaining deadline (not a new one). If the deadline expires, gRPC cancels the entire call chain — all in-flight work is aborted. This prevents cascading timeouts and wasted work during partial outages.

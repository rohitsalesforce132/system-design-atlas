# WebSocket — The Complete Deep Dive

> Everything you need to know about WebSocket: from the wire protocol to scaling to millions of connections. This is how WhatsApp, Slack, Zomato, and Discord deliver real-time messages.

---

## Table of Contents

1. [The Problem WebSocket Solves](#the-problem)
2. [HTTP vs WebSocket — Fundamental Difference](#http-vs-ws)
3. [The Handshake — Step by Step at the Byte Level](#handshake)
4. [Protocol Internals — Frames, Opcodes, Masks](#frames)
5. [Connection Lifecycle](#lifecycle)
6. [Heartbeats — Keeping Connections Alive](#heartbeats)
7. [Scaling WebSocket — The Hard Part](#scaling)
8. [Architecture Patterns for Millions of Connections](#patterns)
9. [Backpressure and Flow Control](#backpressure)
10. [Security — Authentication, Authorization, WSS](#security)
11. [WebSocket vs SSE vs Long Polling — When to Use What](#comparison)
12. [How Real Apps Use WebSocket](#real-apps)
13. [How YOU Can Build This](#build)
14. [Common Interview Questions](#interview)

---

<a id="the-problem"></a>
## The Problem WebSocket Solves

### The Fundamental Limitation of HTTP

HTTP is **request-response**: the client asks, the server answers, the connection closes. The server **cannot push data** to the client on its own.

```
HTTP:
  Client: "Any new messages?"     Server: "No"
  Client: "Any new messages?"     Server: "No"
  Client: "Any new messages?"     Server: "No"
  Client: "Any new messages?"     Server: "Yes! Here's a message"

Problems:
  1. Latency: Client only learns about messages when it asks
  2. Wasted resources: 99% of polls return empty
  3. Battery drain: Mobile devices waste battery polling all night
  4. Not real-time: Average delay = (polling interval / 2)
```

### What WebSocket Does

WebSocket provides a **persistent, bidirectional, full-duplex connection**. Once established, both client and server can send messages to each other at any time, with near-zero latency.

```
WebSocket:
  Client ◄──► Server (connection stays open)
  Server: "You have a new message!"   (pushed instantly, no polling)
  Client: "Thanks, I read it"
  Server: "Another message arrived!" (pushed instantly)
  Client: "Typing..."
  Server: "User B is typing..."      (pushed to other user)

Latency: <5ms per message (after connection is established)
Overhead: ~2-14 bytes per frame (vs ~800 bytes per HTTP request)
```

### Why Not Just Keep HTTP Connections Open?

You could theoretically hold an HTTP connection open. But HTTP has overhead:

```
Every HTTP request includes:
  GET /api/messages HTTP/1.1\r\n
  Host: example.com\r\n
  Authorization: Bearer eyJhbGc...\r\n
  Content-Type: application/json\r\n
  User-Agent: Mozilla/5.0...\r\n
  Accept: application/json\r\n
  Cookie: session=abc123; csrf=xyz789\r\n
  \r\n

  That's ~300-800 bytes of headers PER MESSAGE.

WebSocket frame:
  [1 byte opcode + 1 byte length + 4 byte mask key + payload]

  For "Hello": ~10 bytes total. 80x less overhead.
```

---

<a id="http-vs-ws"></a>
## HTTP vs WebSocket — Fundamental Difference

```
HTTP (Half-Duplex):
                           ┌────────┐
  Client ──request──────►  │ Server │
  Client ◄──response─────  │        │
                           └────────┘
  Connection: closed (or returned to keepalive pool)
  Direction: Client → Server only (server can't initiate)
  Overhead: ~800 bytes per exchange
  Use: "I need data" (request-response)

WebSocket (Full-Duplex):
                           ┌────────┐
  Client ◄──────────────►  │ Server │
  Client ◄──────────────►  │        │     Connection stays open
  Client ◄──────────────►  │        │     Either side sends anytime
  Client ◄──────────────►  │        │     Overhead: ~2-14 bytes
                           └────────┘
  Use: "We need to talk continuously" (real-time)
```

| Aspect | HTTP | WebSocket |
|--------|------|-----------|
| **Connection** | New per request (or pooled) | Single persistent connection |
| **Direction** | Client → Server (one-way) | Bidirectional (both ways) |
| **Overhead per message** | ~800 bytes (headers) | ~2-14 bytes (frame header) |
| **Server push** | Not possible natively | Native — server pushes anytime |
| **Protocol** | HTTP/1.1 or HTTP/2 | WebSocket (over TCP) |
| **Port** | 80 / 443 | 80 / 443 (same!) |
| **Latency** | 50-200ms per exchange | <5ms per message |
| **Max connections** | ~6 per domain (HTTP/1.1) | Unlimited |
| **Browser support** | Universal | Universal (W3C standard since 2011) |
| **Best for** | CRUD, file download, API calls | Chat, gaming, live updates, collaboration |

---

<a id="handshake"></a>
## The WebSocket Handshake — Step by Step

WebSocket doesn't replace HTTP — it **starts as HTTP** and then **upgrades**.

### Step 1: Client Sends Upgrade Request

The client sends a normal HTTP GET request with special headers:

```http
GET /ws/chat HTTP/1.1
Host: chat.example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
Origin: https://example.com
```

What each header does:

```
Upgrade: websocket          → "I want to switch to WebSocket protocol"
Connection: Upgrade         → "This connection should be upgraded"
Sec-WebSocket-Key: ...      → Random base64 string (client-generated)
                               Server uses this to prove it understood the request
Sec-WebSocket-Version: 13   → Protocol version (13 = latest, RFC 6455)
Origin: https://...         → For CORS/security (server can reject)
```

### Step 2: Server Responds with 101 Switching Protocols

```http
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```

The `Sec-WebSocket-Accept` value is **derived from the client's key**:

```
Server computes:
  1. Take client's Sec-WebSocket-Key: "dGhlIHNhbXBsZSBub25jZQ=="
  2. Append magic string: "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
  3. Concatenate: "dGhlIHNhbXBsZSBub25jZQ==258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
  4. SHA-1 hash the concatenation
  5. Base64 encode the hash
  6. Result: "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

Why? This proves the server understood the WebSocket protocol.
If a dumb HTTP server doesn't understand WebSocket, it won't compute
the correct accept key, and the client will close the connection.
```

### Step 3: Connection Upgraded — Now Speaking WebSocket

```
After the 101 response:
  → The TCP connection is NO LONGER HTTP
  → Both sides can now send WebSocket frames
  → The upgrade is complete. No more HTTP semantics.

  Client ────────TCP Connection──────── Server
  (speaking WebSocket frames, not HTTP)
```

### Visual Timeline

```
Client                                Server
  │                                     │
  │── TCP SYN ────────────────────────►│
  │◄── TCP SYN-ACK ───────────────────│
  │── TCP ACK ────────────────────────►│  (TCP connection established)
  │                                     │
  │── GET /ws + Upgrade headers ──────►│  (HTTP request)
  │                                     │
  │◄── 101 Switching Protocols ────────│  (Server agrees)
  │                                     │
  │═════════ WEBSOCKET ACTIVE ═════════│
  │                                     │
  │◄── frame: "Welcome!" ──────────────│  (Server pushes)
  │── frame: {"msg":"Hello"} ─────────►│  (Client sends)
  │◄── frame: {"msg":"Hi there"} ──────│  (Server responds)
  │── frame: ping ────────────────────►│  (Heartbeat)
  │◄── frame: pong ────────────────────│  (Heartbeat reply)
  │                                     │
  │── frame: close ───────────────────►│  (Client closes)
  │◄── frame: close ───────────────────│  (Server acknowledges)
  │                                     │
  │── TCP FIN ────────────────────────►│  (TCP connection closed)
  │◄── TCP FIN-ACK ────────────────────│
```

---

<a id="frames"></a>
## Protocol Internals — Frames, Opcodes, Masks

Once the connection is upgraded, data is sent in **frames**. Each frame has a small header.

### WebSocket Frame Structure

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-------+-+-------------+-------------------------------+
|F|R|R|R| opcode|M| Payload len |    Extended payload length    |
|I|S|S|S|  (4b) |A|     (7b)    |             (16/64 bit)        |
|N|V|V|V|       |S|             |   (if payload len==126 or 127) |
| |1|2|3|       |K|             |                               |
+-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
|     Extended payload length continued, if payload len == 127  |
+ - - - - - - - - - - - - - - - +-------------------------------+
|                               |Masking-key, if MASK set to 1  |
+-------------------------------+-------------------------------+
| Masking-key (continued)       |          Payload Data         |
+-------------------------------- - - - - - - - - - - - - - - - +
:                     Payload Data continued ...                :
+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
|                     Payload Data continued ...                |
+---------------------------------------------------------------+
```

Let me break this down piece by piece:

### FIN (1 bit) — Is this the last fragment?

```
FIN = 1: This frame contains the complete message
FIN = 0: This frame is a fragment; more frames follow

Fragmentation allows sending large messages in chunks:
  Frame 1: FIN=0, opcode=text, "Hello "
  Frame 2: FIN=0, opcode=continuation, "World "
  Frame 3: FIN=1, opcode=continuation, "from WebSocket!"

Reassembled message: "Hello World from WebSocket!"
```

### RSV1, RSV2, RSV3 (3 bits) — Reserved

```
Normally 0. Used for extensions:
  RSV1 = Per-message deflate (compression)
  RSV2/RSV3 = Future extensions
```

### Opcode (4 bits) — What Type of Frame Is This?

```
Opcode  Meaning
──────  ─────────────────────────────────
 0x0    Continuation frame (fragment of a message)
 0x1    Text frame (UTF-8 encoded string)
 0x2    Binary frame (raw bytes — images, files, protobuf)
 0x8    Close frame (shutting down connection)
 0x9    Ping frame (are you alive?)
 0xA    Pong frame (yes, I'm alive!)
 0x3-7  Reserved for future
 0xB-F  Reserved for future

Most apps only use 0x1 (text/JSON) and 0x2 (binary).
```

### MASK (1 bit) — Is the Payload Masked?

```
MASK = 1: Client-to-server messages MUST be masked (security)
MASK = 0: Server-to-client messages are NOT masked

Why mask? To prevent cache poisoning of intermediary proxies.
Without masking, a malicious client could craft a payload that looks
like an HTTP GET request to a proxy, tricking the proxy into caching it.
Masking XORs the payload with a random key so it looks like garbage
to any intermediary.
```

### Payload Length (7 bits, or 16, or 64)

```
payload_len = 0-125:    Length is in these 7 bits directly.
                         (messages up to 125 bytes)
payload_len = 126:      Next 2 bytes are the actual length.
                         (messages 126-65535 bytes)
payload_len = 127:      Next 8 bytes are the actual length.
                         (messages up to 2^64 bytes = 18 exabytes)

This means WebSocket can theoretically send multi-GB messages.
In practice, apps fragment large messages.
```

### Masking Key (4 bytes) — Only in Client→Server Frames

```
The masking key is a random 32-bit value generated by the client.
Each byte of payload is XORed with the masking key:

  masking_key = [0x12, 0x34, 0x56, 0x78]
  payload     = [0x48, 0x65, 0x6C, 0x6C, 0x6F]

  masked[0] = payload[0] XOR masking_key[0 % 4] = 0x48 XOR 0x12 = 0x5A
  masked[1] = payload[1] XOR masking_key[1 % 4] = 0x65 XOR 0x34 = 0x51
  masked[2] = payload[2] XOR masking_key[2 % 4] = 0x6C XOR 0x56 = 0x3A
  masked[3] = payload[3] XOR masking_key[3 % 4] = 0x6C XOR 0x78 = 0x14
  masked[4] = payload[4] XOR masking_key[0 % 4] = 0x6F XOR 0x12 = 0x7D

To unmask: XOR again with the same key.
```

### Complete Example: Sending "Hello"

```
Client sends "Hello" as a text frame:

  FIN=1 | RSV=000 | Opcode=0001 (text) | MASK=1
  Payload length=5 (fits in 7 bits)
  Masking key = [0x12, 0x34, 0x56, 0x78] (random)
  Masked payload = [0x5A, 0x51, 0x3A, 0x14, 0x7D]

  Wire bytes:
  0x81 0x85 0x12 0x34 0x56 0x78 0x5A 0x51 0x3A 0x14 0x7D
  ─┬─ ─┬─ ──────┬────── ──────────┬─────────────────
   │   │         │                  │
   │   │         │                  └── Masked "Hello"
   │   │         └── Masking key (4 bytes)
   │   └── Payload length = 5, MASK=1
   └── FIN=1, Opcode=0001 (text frame)

Total: 11 bytes for a 5-byte message.
Compare: HTTP would need ~800 bytes for the same message.
```

---

<a id="lifecycle"></a>
## Connection Lifecycle

```
States a WebSocket connection goes through:

  CONNECTING (0)
    │
    │ Client sends HTTP Upgrade request
    │
    ▼
  CONNECTING → Server processes upgrade request
    │
    │ Server sends 101 Switching Protocols
    │
    ▼
  OPEN (1)
    │
    │ ┌── Send/receive text frames (JSON messages)
    │ ├── Send/receive binary frames (files, protobuf)
    │ ├── Send ping / receive pong (every 30s)
    │ ├── Fragmentation (large messages in chunks)
    │ └── Subprotocol negotiation (optional)
    │
    │ Either side sends Close frame
    │
    ▼
  CLOSING (2)
    │
    │ Exchange close frames, wait for acknowledgment
    │
    ▼
  CLOSED (3)
    (TCP connection terminated)
```

### Close Frame Details

```
Close frame (opcode 0x8):
  Payload: 2-byte status code + optional reason string

  Common status codes:
  1000: Normal closure
  1001: Endpoint going away (server shutting down)
  1002: Protocol error
  1003: Unsupported data type
  1006: Abnormal closure (no close frame — connection just died)
  1008: Policy violation
  1011: Internal server error
  4000-4999: Application-specific codes
```

### Subprotocol Negotiation

During the handshake, client and server can agree on a **subprotocol**:

```
Client requests:
  GET /ws HTTP/1.1
  Sec-WebSocket-Protocol: chat, superchat

Server picks one:
  HTTP/1.1 101 Switching Protocols
  Sec-WebSocket-Protocol: chat

Now both sides know they're using the "chat" subprotocol.
This means they agree on the JSON schema for messages.
```

---

<a id="heartbeats"></a>
## Heartbeats — Keeping Connections Alive

### Why Heartbeats Are Essential

WebSocket connections are persistent TCP connections. But TCP connections can **silently die**:

```
Silent death scenarios:

1. NAT timeout:
   User's phone connects via WiFi → home router (NAT)
   NAT entries expire after 5 minutes of inactivity
   → Connection dies without either side knowing

2. Mobile network switch:
   User walks from WiFi to 4G
   → IP address changes
   → TCP connection breaks silently

3. Proxy/Load Balancer timeout:
   Corporate proxy closes idle connections after 60 seconds
   → Connection dies without notification

4. Laptop sleep:
   User closes laptop lid
   → TCP connection freezes
   → Neither side knows it's dead
```

Without heartbeats, the server holds **dead connections** indefinitely — wasting memory and file descriptors.

### Ping/Pong Mechanism

```
Every 30 seconds (configurable):

  Server ──ping (opcode 0x9, no payload)──► Client
  Server ◄──pong (opcode 0xA, same payload)── Client

  If pong received within 60 seconds → connection is alive
  If pong NOT received within 60 seconds → connection is dead
    → Close the connection
    → Free memory
    → Clean up resources

Note: Ping/Pong are NOT application messages.
     They are protocol-level control frames.
     The browser handles them automatically.
```

### Idle Timeout

```
Server-side idle timeout configuration:

  idle_timeout = 90 seconds

  If no data received from client for 90 seconds:
    → Send ping
    → Wait 30 seconds for pong
    → If pong received → reset timer
    → If no pong → kill connection
```

### Application-Level Heartbeats

Some apps also implement application-level heartbeats (in addition to protocol-level ping/pong):

```
Every 25 seconds:
  Client ──► {"type": "heartbeat", "timestamp": 1690000000}

  Server ──► {"type": "heartbeat_ack", "server_time": 1690000001}

Why both protocol AND application heartbeats?
  - Protocol ping/pong is invisible to application code
  - Application heartbeat can include server health info
  - Application heartbeat keeps the connection "interesting"
    to NAT/proxies (data flowing = connection stays alive)
```

### The "25-Second Rule" for Mobile

```
Mobile NAT timeouts:
  WiFi NAT:     60 seconds typical
  4G LTE NAT:   30 seconds typical
  3G NAT:       10-15 seconds

Rule of thumb: Send heartbeat every 25 seconds.
This is shorter than most NAT timeouts, keeping connections alive.

WhatsApp sends a heartbeat every ~30 seconds on WiFi,
and every ~15 seconds on mobile data.
```

---

<a id="scaling"></a>
## Scaling WebSocket — The Hard Part

### Why Scaling WebSocket Is Different from HTTP

Scaling HTTP is straightforward: add more servers behind a load balancer. Each request is independent, so any server can handle any request.

WebSocket is **stateful**. A connection lives on **one specific server**. If User A connects to Server 3 and User B connects to Server 7, and User A sends a message to User B:

```
THE CORE PROBLEM:

  Server 3 has User A's connection
  Server 7 has User B's connection

  User A sends message to User B:
    → Message arrives at Server 3
    → Server 3 needs to deliver to User B
    → But User B is on Server 7!
    → Server 3 has NO direct way to reach User B

  How does the message get from Server 3 to Server 7?
```

```
  ┌──────────┐         ┌──────────┐
  │ Server 1  │         │ Server 2  │
  │ Users:    │         │ Users:    │
  │ Alice     │         │ Eve       │
  │ Bob       │         │ Frank     │
  └─────┬────┘         └─────┬────┘
        │                      │
        │     How does Alice   │
        │     message Frank?   │
        │                      │
  ┌─────┴────┐         ┌─────┴────┐
  │ Server 3  │         │ Server 4  │
  │ Users:    │         │ Users:    │
  │ Carol     │         │ George    │
  │ Dave      │         │ Helen     │
  └──────────┘         └──────────┘
```

### Connection Affinity (Sticky Sessions)

The simplest solution: route the same user to the same server.

```
Load Balancer with sticky sessions:
  User A connects to Server 3 → always goes to Server 3

Problem: User A sends message to User B (on Server 7)
  → Server 3 still can't reach Server 7
  → Sticky sessions DON'T solve cross-server messaging
```

**Sticky sessions work for:**
- User-specific data (live dashboards, notifications)
- Stateful sessions

**Sticky sessions FAIL for:**
- Chat between users on different servers
- Multiplayer games with players on different servers
- Any cross-user communication

### The Real Solution: Pub/Sub Message Bus

To send messages between servers, you need a **message bus** (Redis Pub/Sub, Kafka, or RabbitMQ):

```
                    ┌──────────────────────┐
                    │  Redis Pub/Sub       │
                    │  (Message Bus)       │
                    └──────┬───────┬───────┘
                           │       │
          ┌────────────────┘       └────────────────┐
          │                                         │
  ┌───────┴──────┐                          ┌───────┴──────┐
  │  Server 1    │                          │  Server 2    │
  │              │                          │              │
  │  Alice (ws)  │                          │  Eve (ws)    │
  │  Bob (ws)    │                          │  Frank (ws)  │
  └──────────────┘                          └──────────────┘

  Alice wants to send message to Frank:
    1. Alice ──"msg for Frank"──► Server 1
    2. Server 1 publishes to Redis: "frank: {msg}"
    3. Redis broadcasts to all servers subscribed to "frank"
    4. Server 2 receives it (it's subscribed to "frank")
    5. Server 2 sends to Frank via his WebSocket connection

  Works across any number of servers!
```

### Detailed Flow

```
Step 1: Connection setup
  Frank ──ws connect──► Load Balancer ──► Server 2
  Server 2 registers Frank in Redis:
    HSET ws:users:frank server "server-2"

  Server 2 subscribes to Redis channel:
    SUBSCRIBE ws:user:frank

Step 2: Message send
  Alice ──"Hi Frank!"──► Server 1

Step 3: Server 1 looks up Frank
  HGET ws:users:frank server → "server-2"

Step 4: Server 1 publishes to Redis
  PUBLISH ws:user:frank '{"from":"alice","msg":"Hi Frank!"}'

Step 5: Server 2 receives from Redis
  (Server 2 was subscribed to ws:user:frank)
  → Receives message

Step 6: Server 2 delivers to Frank
  Frank's WebSocket connection ──► "Hi Frank!"
```

---

<a id="patterns"></a>
## Architecture Patterns for Millions of Connections

### Problem: The C10M Problem

A single server can handle about **50,000-100,000 concurrent WebSocket connections** (limited by RAM, file descriptors, and CPU).

At WhatsApp/Slack scale, you need **millions** of connections. That means **hundreds of servers**.

```
Scale milestones:
  1 server:      ~65,000 connections
  10 servers:    ~650,000 connections
  100 servers:   ~6,500,000 connections
  1,000 servers: ~65,000,000 connections

WhatsApp:  ~2 billion connections → ~2,000+ servers
Slack:     ~30 million connections → ~500+ servers
Discord:   ~150 million connections → ~1,000+ servers
```

### Connection Server Architecture

```
                              ┌──────────────
  Users ──► DNS (GeoDNS)     │  Redis Pub/Sub  │
  │         │                │  Cluster         │
  │         ▼                │ (message routing)│
  │  Load Balancer (L7)      └──────┬────────────
  │         │                       │
  │    ┌────┼────┐                  │
  │    ▼    ▼    ▼                  │
  │  WS    WS   WS                  │
  │  Srv1  Srv2 Srv3                │
  │    │    │    │                   │
  │    └────┼────┘                  │
  │         │                       │
  │    ┌────▼────────┐              │
  │    │ Presence     │◄─────────────┘
  │    │ Service      │
  │    │ (who's online│
  │    │  and where)  │
  │    └──────────────┘
```

### Component Breakdown

**1. Connection Servers (Gateway Servers)**
```
Purpose: Hold WebSocket connections. Do nothing else.

  Each server:
    - Holds 50,000-100,000 WebSocket connections
    - 16-32 GB RAM (each connection uses ~20-50 KB)
    - Event-loop architecture (Node.js, Go, Erlang, Netty)
    - Does NO business logic — just routes messages

  Memory per connection:
    TCP buffers:         ~8 KB
    WebSocket state:     ~2 KB
    Application data:    ~10-40 KB (user context, subscriptions)
    Total:               ~20-50 KB per connection

  100,000 connections × 50 KB = 5 GB RAM (fits in one server)
```

**2. Presence Service**
```
Purpose: Track who is online and which server holds their connection.

  Redis Hash:
    Key: "presence:user:{user_id}"
    Value: {"server": "ws-server-42", "status": "online", "last_seen": ...}

  When user connects: SET presence
  When user disconnects: DELETE presence
  When someone wants to send a message: GET presence → find server → route
```

**3. Message Bus (Redis Pub/Sub or Kafka)**
```
Purpose: Route messages between connection servers.

  Pattern: Each server subscribes to a Redis channel for each connected user.

  Server 42 has users Alice and Bob connected:
    SUBSCRIBE ws:server:42:user:alice
    SUBSCRIBE ws:server:42:user:bob

  When any server wants to send to Alice:
    PUBLISH ws:server:42:user:alice {"from":"carol","msg":"Hi!"}

  Server 42 receives it and pushes to Alice's WebSocket.
```

### Horizontal Scaling Strategy

```
Adding more capacity = adding more connection servers

  Traffic increases → CPU/RAM on existing servers rises
  → Auto-scaling adds new connection servers
  → Load balancer routes new connections to new servers
  → Old connections stay on old servers (don't disconnect)

  During deployment/upgrade:
  → New server version starts
  → Load balancer drains old server (stops sending new connections)
  → Old server sends "please reconnect" to all connections
  → Clients reconnect → hit load balancer → go to new servers
  → Old server shuts down safely
```

### The "Connection Server" vs "Application Server" Split

```
WRONG: Do everything on one server
  ┌──────────────────────────────┐
  │  Server                      │
  │  ├── WebSocket connections   │
  │  ├── Business logic          │
  │  ├── Database queries        │
  │  └── Message processing      │
  └──────────────────────────────┘
  → Business logic blocks the event loop
  → Can't handle as many connections

RIGHT: Split connection handling from business logic
  ┌──────────────────────┐
  │  Connection Server   │     ┌──────────────────────┐
  │  (Just WebSocket)    │────►│  Application Server   │
  │                      │     │  (Business logic,     │
  │  - Hold connections  │◄────│   DB queries,         │
  │  - Route messages    │     │   message processing) │
  │  - Heartbeats        │     │                      │
  └──────────────────────┘     └──────────────────────┘
  Via Redis Pub/Sub,         Handles messages from
  internal gRPC, or          connection servers,
  internal Kafka             processes them, returns
                             results
```

---

<a id="backpressure"></a>
## Backpressure and Flow Control

### The Problem

What happens when a server sends messages faster than a client can receive them?

```
Server sends 10,000 messages/sec to a mobile client on 3G
Client can only process 100 messages/sec
→ Messages pile up in server's send buffer
→ Server runs out of RAM
→ Server crashes

This is called BACKPRESSURE.
```

### WebSocket Backpressure Handling

```
WebSocket is built on TCP. TCP has built-in flow control:

  TCP Send Buffer (server):  [msg][msg][msg][msg][msg]...
  ──────────────────────────────────────────────────────
  TCP Receive Buffer (client): [msg][msg]  (slowly draining)

  When client's receive buffer is full:
    → TCP tells server: "Stop sending" (receive window = 0)
    → Server's send buffer starts filling
    → When server's send buffer is full:
      → write() call BLOCKS (or returns EAGAIN)

  In Node.js:
    ws.send("message") returns false
    → Buffer is full!
    → Don't send more until 'drain' event fires
```

### Handling Backpressure in Code

```javascript
// Node.js example: respecting backpressure
function sendMessage(ws, message) {
    const canSend = ws.send(message);

    if (!canSend) {
        // Buffer is full — client can't keep up
        // Options:
        // 1. Buffer the message and send later
        // 2. Drop the message (for non-critical data)
        // 3. Close the connection (if too far behind)

        pendingMessages.push(message);
        ws.once('drain', () => {
            // Client caught up — resume sending
            const pending = pendingMessages.splice(0);
            pending.forEach(msg => sendMessage(ws, msg));
        });
    }
}
```

### Strategies for Different Apps

| App Type | Strategy |
|----------|----------|
| Chat (WhatsApp) | Buffer messages, deliver when client catches up. Close connection if buffer > 10MB. |
| Live scores | Drop old scores, send only the latest. No need to queue 100 score updates. |
| Stock ticker | Drop oldest data, keep only latest N prices. Time-sensitive data = drop stale. |
| Collaboration | Buffer everything (can't lose edits). Use operational transforms. |

---

<a id="security"></a>
## Security — Authentication, Authorization, WSS

### Authentication (Who Are You?)

WebSocket doesn't have a standard auth mechanism. The handshake is a single HTTP request, so you authenticate during the handshake:

**Method 1: Token in Query Parameter**
```
const ws = new WebSocket('wss://api.example.com/ws?token=eyJhbGc...');

Server validates token during handshake:
  1. Extract token from URL
  2. Verify JWT signature
  3. Check expiry
  4. If valid → upgrade to WebSocket
  5. If invalid → return 401 Unauthorized
```

**Method 2: Auth Cookie (Sent with Handshake)**
```
Browser automatically sends cookies with the WebSocket handshake:
  GET /ws HTTP/1.1
  Cookie: session=abc123

Server reads cookie, validates session, upgrades if valid.
This works because WebSocket handshake IS an HTTP request.
```

**Method 3: Sec-WebSocket-Protocol Header**
```
Client sends:
  Sec-WebSocket-Protocol: bearer,eyJhbGc...

Server extracts token from the protocol header.
```

### WSS (WebSocket Secure = WebSocket over TLS)

```
WS (unencrypted):
  Client ──TCP──► Server
  → Anyone on the network can read messages
  → Like HTTP without HTTPS
  → NEVER use in production

WSS (encrypted):
  Client ──TLS──► Server
  → All messages encrypted
  → Like HTTPS for HTTP
  → ALWAYS use in production

WSS handshake:
  1. TCP connection established
  2. TLS handshake (encrypt the connection)
  3. HTTP Upgrade request (over encrypted channel)
  4. WebSocket frames (all encrypted)
```

### CORS and Origin Checking

```
Server checks the Origin header during handshake:

  Origin: https://evil.com  → REJECT (cross-origin attack)
  Origin: https://app.com   → ACCEPT (your own app)
```

### Rate Limiting

```
Messages per user per second:
  → Normal: 10 messages/sec
  → Suspicious: 100+ messages/sec → rate limit or disconnect

  Implementation:
    user_msg_count = redis.incr("rate:user:{id}:{minute}")
    if user_msg_count > 100:
        ws.close(1008, "Rate limit exceeded")
```

---

<a id="comparison"></a>
## WebSocket vs SSE vs Long Polling — When to Use What

| Feature | WebSocket | SSE (Server-Sent Events) | Long Polling |
|---------|-----------|--------------------------|--------------|
| **Direction** | Bidirectional (both ways) | Server → Client only | Server → Client only |
| **Protocol** | WebSocket over TCP | HTTP | HTTP |
| **Connection** | Single persistent | Single persistent | New per poll |
| **Overhead** | ~2-14 bytes/message | ~5-10 bytes/message | ~800 bytes/request |
| **Latency** | <5ms | <5ms | Depends on poll interval |
| **Reconnection** | Manual (app must implement) | Auto (built into EventSource) | Manual |
| **Binary data** | Yes (opcode 0x2) | No (text only) | No |
| **Browser support** | WebSocket API | EventSource API | XMLHttpRequest |
| **Max connections** | Unlimited | 6 per domain (HTTP/1.1) | 6 per domain |
| **Proxy friendly** | Often blocked by corporate proxies | Yes (standard HTTP) | Yes |
| **Best for** | Chat, gaming, collaboration | Notifications, live scores | Legacy fallback |

### Decision Guide

```
Does the client need to SEND data back?

  YES (chat, gaming, collaboration)
  → WebSocket

  NO (just receiving updates)
  → SSE (simpler, auto-reconnect, HTTP-based)
  → WebSocket only if you need binary data or >6 streams

Legacy browser / strict corporate proxy?
  → Long Polling (works everywhere)
```

---

<a id="real-apps"></a>
## How Real Apps Use WebSocket

### WhatsApp — 2 Billion Connections

```
WhatsApp's WebSocket-like Architecture (custom protocol):

  Phone ──TLS──► Connection Server (Erlang)

  Erlang was chosen because:
    - Lightweight processes (2KB per process vs 2MB per OS thread)
    - 1 process per connection (100,000+ processes per server)
    - Built-in distribution (nodes can talk to each other natively)
    - Hot code swapping (upgrade without disconnecting users)
    - Proven in telecom (Erlang was built for phone switches)

  Message flow:
    Alice sends message to Bob:
    1. Alice ──► Her connection server (Erlang node A)
    2. Node A looks up Bob's server in presence service
    3. Node A sends message to Bob's server via Erlang distribution
    4. Bob's server ──► Bob's phone

  If Bob is offline:
    1. Message stored in database
    2. When Bob connects, all pending messages delivered
    3. Messages stored in Bob's phone SQLite DB

  Scale:
    - 2 billion+ connections
    - ~200,000 Erlang processes per server
    - Messages delivered in <200ms globally
    - ~1,000+ connection servers worldwide
```

### Slack — Real-Time Workspace

```
Slack's WebSocket Architecture:

  Browser ──WSS──► Slack Gateway (Load Balancer)
                        │
                        ▼
                   Connection Server
                   (Go, event-loop)
                        │
              ┌─────────┼─────────┐
              │         │         │
              ▼         ▼         ▼
           Redis     PostgreSQL  Worker
           (pub/sub  (message    (business
            routing)  history)   logic)

  Key design:
    - One WebSocket per user per device
    - Each connection server handles ~50K connections
    - Redis Pub/Sub routes messages between servers
    - Messages persisted to PostgreSQL for history
    - Typing indicators, presence, reactions — all via WebSocket
```

### Discord — 150 Million Connections

```
Discord's WebSocket Architecture:

  Client ──WSS──► Gateway ──► Connection Server (ScyllaDB / Erlang→Go)
                      │
                      ├── Presence Service (Redis)
                      ├── Message Bus (Kafka)
                      └── API Servers (Microservices)

  Discord's evolution:
    1. Started with Erlang (like WhatsApp)
    2. Moved to Go for better performance and tooling
    3. Uses ScyllaDB (C++ Cassandra clone) for message storage
    4. ~5 million concurrent connections per Go server

  Discord's scale numbers:
    - 150 million monthly active users
    - ~10 million peak concurrent connections
    - 4 billion+ messages per day
    - Each user has one WebSocket connection
```

### Zomato — Live Order Tracking

```
Zomato's WebSocket Use:

  Customer App ──WSS──► Zomato Gateway
                              │
                              ├── Order Service (order status)
                              ├── Tracking Service (rider GPS)
                              └── Notification Service

  What goes over WebSocket:
    - Rider location updates (every 5 seconds)
    - Order status changes (confirmed → cooking → picked up → delivered)
    - ETA updates (recalculated as rider moves)

  Rider App ──WSS──► Zomato Gateway
    - Sends GPS coordinates every 5 seconds
    - Receives new delivery assignments
    - Sends status updates (picked up, delivered)

  Scale: ~100,000+ concurrent connections during peak hours
```

---

<a id="build"></a>
## How YOU Can Build This

### Level 1: Simple Chat (Single Server)

```javascript
// Server: Node.js + ws library
const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 8080 });

const clients = new Map();  // user_id → ws connection

wss.on('connection', (ws, req) => {
    const userId = req.url.split('token=')[1]; // simplified auth

    clients.set(userId, ws);  // register connection

    ws.on('message', (data) => {
        const msg = JSON.parse(data);
        const targetWs = clients.get(msg.to);

        if (targetWs && targetWs.readyState === WebSocket.OPEN) {
            targetWs.send(JSON.stringify({
                from: userId,
                msg: msg.text,
                timestamp: Date.now()
            }));
        }
    });

    ws.on('close', () => {
        clients.delete(userId);  // unregister
    });
});

// Client: Browser
const ws = new WebSocket('ws://localhost:8080?token=user123');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.send(JSON.stringify({ to: 'user456', text: 'Hello!' }));
```

### Level 2: Multi-Server Chat (with Redis Pub/Sub)

```javascript
// Each connection server runs this
const WebSocket = require('ws');
const redis = require('redis');

const wss = new WebSocket.Server({ port: 8080 });
const pubClient = redis.createClient();
const subClient = redis.createClient();

const localClients = new Map();  // user_id → ws (THIS server only)

wss.on('connection', (ws, req) => {
    const userId = getUserIdFromRequest(req);
    localClients.set(userId, ws);

    // Subscribe to this user's channel
    subClient.subscribe(`ws:user:${userId}`);

    ws.on('message', (data) => {
        const msg = JSON.parse(data);

        // Check if target is on THIS server
        const localTarget = localClients.get(msg.to);
        if (localTarget) {
            localTarget.send(data);
        } else {
            // Target is on another server → publish to Redis
            pubClient.publish(
                `ws:user:${msg.to}`,
                JSON.stringify({ from: userId, msg: msg.text })
            );
        }
    });

    // Listen for messages from other servers (via Redis)
    subClient.on('message', (channel, message) => {
        const userId = channel.split(':')[2];
        const ws = localClients.get(userId);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(message);
        }
    });

    ws.on('close', () => {
        localClients.delete(userId);
        subClient.unsubscribe(`ws:user:${userId}`);
    });
});
```

### Level 3: Production Architecture

```
  Users
    │
    ▼
  Cloudflare (WSS termination, DDoS protection)
    │
    ▼
  AWS ALB (load balancer, sticky sessions)
    │
    ├──► Connection Server 1 (Node.js / Go)
    ├──► Connection Server 2
    ├──► Connection Server N...
    │
    │ (via Redis Pub/Sub or Kafka)
    │
    ├──► Presence Service (Redis)
    ├──► Message Service (PostgreSQL for history)
    └── Notification Service (Push notifications)
```

**Tech choices for connection servers:**

| Language | Pros | Cons | Used By |
|----------|------|------|---------|
| **Node.js** | Easy WebSocket support, huge ecosystem | Single-threaded, GC pauses | Slack (partially), many startups |
| **Go** | Goroutines (lightweight), compiled, fast | Less mature WS ecosystem | Discord, Uber |
| **Erlang** | Millions of lightweight processes, fault-tolerant | Steep learning curve | WhatsApp, Discord (originally) |
| **Java/Netty** | Enterprise-grade, fast, battle-tested | Heavyweight (JVM), high memory | LinkedIn, some financial apps |
| **Rust** | Zero-cost abstractions, memory-safe | Steep learning curve | Emerging in real-time systems |
```

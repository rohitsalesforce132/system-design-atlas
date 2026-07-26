# Networking Protocols & Communication Patterns

## What This Is

Every app in this atlas ultimately comes down to **bytes moving across a wire**. WhatsApp pushes messages over a long-lived socket. Zoom sends video over UDP. Paytm's Soundbox waits silently on an MQTT connection until a payment lands. Netflix ships 4K video in 4-second chunks. The choice of *which wire protocol* dictates your latency, your battery life, your server cost, and how many users one server can hold.

This guide covers **every networking protocol and communication pattern used across the system design atlas**. For each one: what it is (plain English + analogy first), how it works (handshakes, lifecycles, ASCII diagrams), key features, when to use it, and the real apps using it.

```
THE DECISION TREE (read this first):

  Do you need the server to push data to the client?
  │
  ├─ No  → Plain HTTP/1.1 or HTTP/2 request/response. Stop here.
  │
  ├─ Yes → Is it one-way (server → client only)?
  │        │
  │        ├─ Yes, simple → SSE (Server-Sent Events)
  │        ├─ Yes, but IoT/constrained → MQTT
  │        └─ No, bidirectional → WebSocket
  │
  ├─ Is it real-time audio/video/media?
  │        → WebRTC (or custom UDP, like Zoom's)
  │
  ├─ Is it service-to-service (internal)?
  │        → gRPC (protobuf over HTTP/2)
  │
  └─ Is it a legacy browser that can't do WebSocket?
           → Long Polling (fallback only)
```

---

## 1. WebSocket

### What It Is (Analogy)

**A phone call.** Once you dial and the other person picks up, the line stays open. Either side can speak at any time, instantly, without dialing again. You only hang up when the conversation is done.

Contrast with HTTP, which is a **text message**: you compose, send, wait for a reply, and the connection closes. To ask a follow-up, you open a new message. WebSocket is the open phone call; HTTP is the SMS.

### How It Works (Connection Lifecycle)

A WebSocket connection *starts life* as a normal HTTP request, then gets "upgraded" into a permanent TCP connection.

```
STEP 1 — Client sends an HTTP Upgrade request:

  Client ────────────────────────────────────► Server
    GET /chat HTTP/1.1
    Host: api.example.com
    Upgrade: websocket            ← "I want to switch protocols"
    Connection: Upgrade
    Sec-WebSocket-Key: dGhlIHNh...  ← random base64 key
    Sec-WebSocket-Version: 13

STEP 2 — Server agrees and responds with 101 Switching Protocols:

  Client ◄──────────────────────────────────── Server
    HTTP/1.1 101 Switching Protocols
    Upgrade: websocket
    Connection: Upgrade
    Sec-WebSocket-Accept: s3pPLMBi...  ← hash of the key + magic GUID

STEP 3 — The TCP connection is now a WebSocket. Both sides can send
         "frames" at any time, in either direction:

  Client ◄════════════════════════════════════► Server
         (full-duplex binary/text frames)

STEP 4 — Keepalive (ping/pong). Because proxies and load balancers
         will silently kill "idle" TCP connections after 30–60s:

  Client ◄──── ping (0x89) ──── Server        "you alive?"
  Client ──── pong (0x8A) ────► Server        "yes"

  (Usually every 20–30 seconds. If pong doesn't come back,
   the connection is considered dead and must be re-established.)

STEP 5 — Close handshake:

  Client ──── close frame (0x88) ────► Server
  Client ◄─── close frame (0x88) ──── Server
  TCP connection torn down.
```

**The magic GUID:** The server proves it understood the upgrade by computing `Sec-WebSocket-Accept = base64(SHA1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))`. That fixed string is defined in RFC 6455. It's a defence against cross-protocol attacks.

### Key Features

| Feature | What it means |
|---|---|
| **Full-duplex** | Both client and server send independently, at the same time, no request needed. |
| **Single TCP connection** | One handshake, then it stays open. No per-message TCP/TLS setup cost. |
| **Low latency after connect** | Messages are just frames on an open pipe — sub-millisecond push. |
| **Binary + text frames** | Can send raw bytes (protobuf, images) or JSON strings. |
| **Built on TCP** | Reliable, ordered delivery. What you send arrives, in order. |
| **Ping/pong keepalive** | Detects dead connections; keeps the path warm through proxies. |
| **Message framing** | Unlike raw TCP (a byte stream), WebSocket knows "this is one complete message." |

**Cost reality:** The expensive part is *holding* the connection. Each open socket ≈ tens of KB of server RAM (buffers + TLS state + connection state). This is why WhatsApp's architecture is dominated by "how many sockets can one Erlang VM hold" — see [whatsapp.md](../apps/whatsapp.md).

### When to Use It

- **Chat / messaging** (WhatsApp, Instagram DMs, Slack) — bidirectional, real-time.
- **Live dashboards** (trading, ops, fleet tracking) — server pushes updates; client sends commands.
- **Multiplayer games** (browser-based) — low-latency two-way state sync.
- **Collaborative editing** (Google Docs, Figma) — real-time cursor + edits.
- **Ride/driver dispatch** (Uber, Ola) — driver app holds a socket; dispatcher pushes ride offers.
- **Notifications that need a back-channel** (typing indicators, read receipts, reactions).

**Don't use it when:** the client only needs to *receive* updates (use SSE — simpler, auto-reconnects, plays nice with HTTP infra), or for request/response patterns (plain HTTP is fine).

### Real Apps Using It (and How)

- **WhatsApp** ([whatsapp.md](../apps/whatsapp.md)) — Every online device holds a long-lived WebSocket-like socket to an Erlang chat server. Messages are pushed, never polled. A single Erlang VM holds hundreds of thousands of concurrent connections because each is a tiny ~2KB green-thread process.
- **Ola / Uber** ([ola.md](../apps/ola.md)) — Driver apps hold WebSockets to "connection servers." When Dispatch needs to ping 5 candidate drivers, it fans out 5 messages through those servers. Live driver GPS positions stream back over the same sockets.
- **Instagram** ([instagram.md](../apps/instagram.md)) — DMs delivered over WebSockets; Redis tracks presence.
- **Twitter/X** ([twitter.md](../apps/twitter.md)) — Live timeline updates and notification stream via WebSocket / SSE.
- **Spotify** ([spotify.md](../apps/spotify.md)) — Collaborative playlists update in real-time over WebSocket (Socket.io).
- **Zoom** ([zoom.md](../apps/zoom.md)) — Signaling channel ("you're muted," "new person joined") runs over WebSocket/TCP; the media itself is separate (see WebRTC).

---

## 2. Server-Sent Events (SSE)

### What It Is (Analogy)

**A radio broadcast.** You tune in once, and the station keeps sending music. You can't call the DJ back — it's one-way — but you don't have to keep requesting the next song. If you lose signal, the radio automatically re-tunes to the right spot and keeps playing.

### How It Works

SSE is just HTTP, but the server *never closes the response*. The browser opens a long-lived HTTP GET, and the server writes a stream of `text/event-stream` chunks.

```
Client ──── GET /live-updates HTTP/1.1 ────►  Server
            Accept: text/event-stream

Server ◄──── HTTP/1.1 200 OK ──────────────  (response stays OPEN)
            Content-Type: text/event-stream
            Cache-Control: no-cache

            data: {"price": 150}\n\n          ← event 1
            (server keeps writing...)

            data: {"price": 151}\n\n          ← event 2

            data: {"price": 149}\n\n          ← event 3
            ...connection stays open indefinitely...
```

**The EventSource API (browser):**
```javascript
const source = new EventSource('/live-updates');

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("Got:", data);
};

// Auto-reconnects on disconnect. The browser sends
// Last-Event-ID so the server can resume from where it left off.
```

**Event format** is dead simple:
```
id: 42
event: trade
data: {"symbol":"RELIANCE","price":2840}

(blank line = end of event)
```

### Key Features

| Feature | What it means |
|---|---|
| **One-way (server → client)** | Server pushes; client just listens. Client cannot send back over the same stream. |
| **Pure HTTP** | Works through every proxy, firewall, and load balancer. No Upgrade dance. |
| **Auto-reconnect** | Browser's `EventSource` reconnects automatically and resumes via `Last-Event-ID`. |
| **Text only** | Sends UTF-8 text. For binary, base64-encode or use WebSocket. |
| **Browser-native API** | No library needed — `new EventSource(url)`. |
| **Connection limit** | Browsers cap ~6 concurrent SSE connections per domain over HTTP/1.1 (HTTP/2 lifts this). |

### When to Use It

- **Stock tickers, live scores, news feeds** — pure server→client push.
- **Notification bells** (Twitter, Facebook notification count).
- **Live blog / activity feeds** — append-only stream of events.
- **AI streaming responses** (LLM token streaming) — ChatGPT-style "typing" output.
- **Status updates** — build progress, deploy logs, order tracking.

**Rule of thumb:** If the client only needs to *listen*, SSE beats WebSocket — it's simpler, auto-reconnects, and uses existing HTTP infra. If the client must also *send* (typing, reactions), use WebSocket or pair SSE with a normal POST for the uplink.

### Real Apps Using It

- **Twitter/X** ([twitter.md](../apps/twitter.md)) — Notification stream and live tweet delivery often via SSE alongside WebSocket.
- **Facebook** ([facebook.md](../apps/facebook.md)) — Real-time notifications and feed updates via long-lived HTTP streams / SSE.
- **ChatGPT / LLM UIs** — Token-by-token streaming responses use SSE under the hood.

---

## 3. gRPC

### What It Is (Analogy)

**A drive-through with a fixed menu.** You don't argue with the speaker about format — the menu lists exactly what you can order and what you'll get back, in a rigid structure. You speak a compact, agreed-upon code, and the kitchen responds in kind. Fast, typed, no ambiguity.

Compare to REST/JSON, which is like **ordering in free-form English at a sit-down restaurant** — flexible, but you spend time negotiating and the response is verbose.

### How It Works

gRPC runs on **HTTP/2** and uses **Protocol Buffers (protobuf)** as its serialization format. You define your service in a `.proto` file; the toolchain generates client + server stubs in 11+ languages.

```
STEP 1 — Define the contract (.proto file):

  syntax = "proto3";
  service OrderService {
    rpc CreateOrder (OrderRequest) returns (OrderResponse);
    rpc TrackOrder (OrderId) returns (stream OrderStatus);   ← server streaming
    rpc UploadLogs (stream LogLine) returns (Summary);        ← client streaming
    rpc Chat (stream Message) returns (stream Message);       ← bidirectional
  }

STEP 2 — Code generation produces typed stubs in Python, Go, Java, etc.

STEP 3 — Client calls the stub as if it were a local function:
  response = orderService.CreateOrder(request)

STEP 4 — Under the hood, over HTTP/2:

  Client ──────────────────────────────────────► Server
    POST /OrderService/CreateOrder HTTP/2
    Content-Type: application/grpc
    Content-Length: 18          ← protobuf-encoded body, tiny
    <binary protobuf bytes>

  Client ◄────────────────────────────────────── Server
    HTTP/2 HEADERS + DATA frame
    grpc-status: 0
    <binary protobuf response>
```

**Protobuf vs JSON size:** Protobuf encodes fields by number, not name, and uses variable-length integers.
```
JSON:        {"user_id": 42, "status": "active"}   → 35 bytes
Protobuf:    <field1=42><field2="active">           → ~10 bytes
```

### The Four Streaming Modes

gRPC over HTTP/2 supports four call types — this is its superpower over plain REST:

```
1. UNARY (one request → one response)         [like normal RPC/REST]
   Client ──► req  ──────────── resp ◄── Server

2. SERVER STREAMING (one request → stream of responses)
   Client ──► req  ──────────── resp ◄── Server
                               ──────────── resp ◄── Server
                               ──────────── resp ◄── Server

3. CLIENT STREAMING (stream of requests → one response)
   Client ──► req  ────────────            Server
          ──► req  ────────────            Server
          ──► req  ──────────── resp ◄── Server

4. BIDIRECTIONAL STREAMING (both stream, independently)
   Client ◄════════════════════════════════► Server
         (full-duplex over one HTTP/2 stream)
```

### Key Features

| Feature | What it means |
|---|---|
| **HTTP/2 transport** | Multiplexed, header-compressed, single TCP connection per host. |
| **Protobuf serialization** | Compact binary, strongly typed, schema-evolvable. |
| **Bi/uni/streaming** | All four call patterns supported natively. |
| **Code generation** | Type-safe clients/servers in Go, Java, Python, C++, Rust, Node, etc. |
| **Bi-directional streaming** | Great for chat-like service-to-service patterns. |
| **Built-in deadlines, timeouts, cancellation** | First-class. |
| **Health checking, interceptors, metadata** | Production-grade plumbing. |

**Caveats:** gRPC is *service-to-service*. Browsers can't speak raw gRPC easily (HTTP/2 trailers, etc.), so there's **gRPC-Web** (a proxy translates). For browser-facing real-time, WebSocket/SSE remains simpler.

### When To Use It

- **Internal microservice-to-microservice calls** — the dominant use case.
- **Polyglot environments** — proto contract + generated stubs in every language.
- **High-throughput, low-latency internal APIs** — protobuf + HTTP/2 is ~5–10x faster than JSON+HTTP.
- **Streaming telemetry / logs / metrics** between services.
- **Mobile → backend** (with gRPC-Web or mobile-native gRPC).

**Don't use for:** browser-direct public APIs (use REST/JSON or gRPC-Web), or human-readable debugging needs.

### Real Apps Using It

- **Spotify** ([spotify.md](../apps/spotify.md)) — RPC framework is gRPC + Protocol Buffers for internal service calls; custom HTTP APIs for clients.
- **Netflix** ([netflix.md](../apps/netflix.md)) — Services communicate over HTTP/gRPC or async Kafka.
- **Twitter/X** ([twitter.md](../apps/twitter.md)) — Internally uses Thrrift/gRPC over Finagle (historically) + Envoy service mesh.
- **Google** (Stackdriver, GCP) — Most Google APIs are gRPC under the hood; the JSON REST versions are generated from the same proto.

---

## 4. WebRTC

### What It Is (Analogy)

**Two people standing in the same room talking.** No switchboard operator, no central relay — direct voice, in real time, even if the building's main phone line is jammed. The hard part is *finding the room* (signaling); once both parties are in it, the conversation is peer-to-peer.

This is fundamentally different from everything else in this guide. WebSocket, HTTP, MQTT — all route through a server. WebRTC tries to connect two browsers (or a browser and a media server) **directly**, bypassing your servers for the actual media. Your servers are only the matchmaker.

### How It Works (The Three-Phase Handshake)

WebRTC media delivery has three sub-problems: (1) **what** media to exchange, (2) **how** to reach each other through NAT/firewalls, (3) the actual media flow. Phases: **signaling → ICE → media**.

```
PHASE 1 — SIGNALING (exchange "what we want to send")
   Done over any channel: WebSocket, HTTP, even smoke signals.
   Peers exchange SDP (Session Description Protocol) offers/answers.

   Alice                                              Bob
     │                                                │
     ├─ createOffer() ──► SDP offer ──── (over WebSocket/HTTP) ────► │
     │                                                                │
     │ ◄────────── SDP answer ──── (over WebSocket/HTTP) ─────────────┤
     │

   SDP contains: codecs (Opus, VP8/VP9/H.264), resolutions,
                 media types (audio/video/data), encryption keys (DTLS-SRTP).

PHASE 2 — ICE (find a network path through NAT/firewalls)

   Each side gathers "candidates" — possible addresses the other can reach:

   Alice's candidates:                  Bob's candidates:
   - host: 192.168.1.5:50000            - host: 10.0.0.7:60000
   - srflx: 203.0.113.9:50000 (STUN)    - srflx: 198.51.100.2:60000 (STUN)
   - relay: turn.example.com:3478 (TURN) - relay: turn.example.com:3478 (TURN)

   They try each pair (connectivity checks) until one works:

   Alice ──"can you reach me at 203.0.113.9?"──► Bob
   Bob   ──"yes!"────────────────────────────────► Alice

PHASE 3 — MEDIA FLOWS (directly, peer-to-peer, over UDP/SRTP)

   Alice ◄══════════════════════════════════════► Bob
         encrypted audio/video (DTLS-SRTP)
         + data channel (SCTP over DTLS)
```

### STUN, TURN, and ICE Explained

Most devices sit behind NAT (your home router) and a firewall — they don't have a public IP, so the other peer can't just "dial" them.

```
STUN (Session Traversal Utilities for NAT):
  "What's my public IP?"
  Client ──► STUN server ──► "You're 203.0.113.9:50000 from my view"
  Cheap, stateless. Works for ~80% of connections (home NAT, cone NAT).

TURN (Traversal Using Relays around NAT):
  "I can't reach the peer directly. Relay for me."
  Client ──► TURN server ◄── Client
  Server relays all media. Expensive (you pay bandwidth). ~20% of connections.
  Symmetric NAT, enterprise firewalls usually need TURN.

ICE (Interactive Connectivity Establishment):
  The framework that tries host → STUN → TURN in order, picks the best path.
```

```
          Direct P2P (~80% of calls)
          Alice ◄══════════════════► Bob
          (cheap: your servers see almost no media traffic)

          Relayed via TURN (~20% of calls)
          Alice ───► TURN server ◄─── Bob
          (expensive: you relay every video frame)
```

### SFU vs P2P (Why Group Calls Don't Stay P2P)

```
P2P (mesh): works for 2–4 people. Each peer sends to every other.

  For N people, each sends N-1 copies of their video.
  4 people: each uploads 3 video streams. Bandwidth = O(N²).
  Falls apart fast.

SFU (Selective Forwarding Unit): one server receives each sender's
  stream once and forwards selectively. Zoom, Meet, Teams use this.

  Each participant ──► SFU (one upload)
  SFU ──► each participant (tailored, simulcast layer chosen)

  Bandwidth per participant = O(1) up + O(N-1) down.
  The SFU is the "media server" — that's why Zoom has MMR servers.
```

### Key Features

| Feature | What it means |
|---|---|
| **Peer-to-peer media** | Audio/video flows directly between browsers when possible — sub-100ms latency. |
| **UDP transport (RTP/SRTP)** | Real-time; tolerates packet loss (a dropped video frame is fine, late is worse). |
| **Data channels** | Arbitrary binary data P2P (chat, file transfer, game state) via SCTP over DTLS. |
| **Mandatory encryption** | DTLS-SRTP — every WebRTC stream is encrypted, always. |
| **Adaptive bitrate** | Browser adjusts video quality based on bandwidth in real time. |
| **Simulcast** | Sender emits multiple resolutions; SFU picks the right one per viewer. |

### When To Use It

- **Video / voice calls** (1:1 or small group) — Zoom, Meet, WhatsApp calls.
- **Live streaming with sub-second latency** — Twitch-style, but note most large-scale live uses HLS/DASH for scale (see [cdn.md](../concepts/cdn.md)).
- **Screen sharing.**
- **P2P data transfer** — file sharing, collaborative canvas, low-latency game data.
- **Cloud gaming / remote desktop** — input up, frames down.

**Don't use for:** VOD (use HLS/DASH + CDN), or anything needing guaranteed delivery/order (use TCP-based protocols).

### Real Apps Using It

- **Zoom** ([zoom.md](../apps/zoom.md)) — Historically used a *custom UDP protocol* (not pure WebRTC) for tighter control over FEC and congestion. Browser-based Zoom uses WebRTC. Principles identical: SFU (their "MMR" servers) + signaling over WebSocket + UDP media.
- **WhatsApp** — Voice/video calls use WebRTC-like P2P with relay fallback (WhatsApp runs their own relay infrastructure).
- **Google Meet / Duo** — Pure WebRTC with Google's SFU.
- **YouTube Live** ([youtube.md](../apps/youtube.md)) — Live ingest via RTMP/WebRTC; low-latency viewing via LL-HLS/WebRTC.

---

## 5. MQTT

### What It Is (Analogy)

**A newspaper subscription system.** You don't call the printing press every morning asking "any news?" You subscribe to "Sports → Cricket" and the press delivers exactly that section to your doorstep whenever it's published. The press doesn't know or care who you are — it just publishes to topics, and the distribution system routes each copy to whoever subscribed.

This is the **publish/subscribe** model, and MQTT is the leanest, meanest protocol built for it — designed in 1999 to run over satellite links with intermittent connectivity and tiny bandwidth. It powers almost every IoT deployment on Earth.

### How It Works

MQTT decouples senders from receivers via a **broker**. Clients never talk to each other directly.

```
                    ┌──────────────────┐
  Publisher ───────►│                  │
  (sensor)          │   MQTT BROKER    │
   publish to       │   (e.g. EMQX,    │
   "device/temp"    │    Mosquitto)    │
                    │                  │
                    │  topic tree:     │
                    │  home/           │
                    │   living/        │
                    │    temp ◄──sub───┼──── Subscriber A (dashboard)
                    │    temp ◄──sub───┼──── Subscriber B (alarm)
                    │   kitchen/       │
                    │    temp          │
                    └──────────────────┘

  The publisher has no idea who reads its data.
  The subscriber has no idea who produces it.
  The broker matches by topic.
```

**Connection lifecycle:**

```
Client ──CONNECT──► Broker         (TCP + optional TLS)
Broker ──CONNACK──► Client         (connection accepted)

Client ──SUBSCRIBE "home/living/temp" QoS 1──► Broker
Broker ──SUBACK──► Client

(publisher sends)
Publisher ──PUBLISH "home/living/temp" "22.5C"──► Broker
Broker ──PUBLISH "home/living/temp" "22.5C"──► Subscriber

Client ──PINGREQ──► Broker   (keepalive, every N seconds)
Broker ──PINGRESP──► Client

Client ──DISCONNECT──► Broker
```

### QoS (Quality of Service) Levels — The Killer Feature

MQTT defines three delivery guarantees. This is why it beats plain TCP for IoT — you choose per-message reliability.

```
QoS 0 — At most once (fire and forget)
  Publisher ──PUBLISH──► Broker (done, no ACK)
  Fastest, no retry. Lost messages stay lost.
  Use: telemetry you sample every second — losing one is fine.

QoS 1 — At least once (acknowledged, may duplicate)
  Publisher ──PUBLISH──► Broker
  Broker    ──PUBACK───► Publisher  (if no ACK, re-send)
  Guaranteed delivery, but receiver may see duplicates (must dedupe by msg ID).
  Use: payment notifications, alerts. The default for most apps.

QoS 2 — Exactly once (four-way handshake, no dupes)
  Publisher ──PUBLISH──► Broker
  Broker    ──PUBREC───► Publisher
  Publisher ──PUBREL───► Broker
  Broker    ──PUBCOMP──► Publisher
  Slowest but exactly-once. Use: billing events, money.
```

Plus **Retained Messages** (broker stores the last message on a topic; new subscribers get it immediately) and **Last Will & Testament** (broker publishes a preset message if a client disconnects ungracefully — how IoT detects "device went offline").

### Key Features

| Feature | What it means |
|---|---|
| **Publish/subscribe** | N-to-M decoupling. Publishers and subscribers don't know each other. |
| **Tiny wire format** | Header is 2 bytes. A publish can be <20 bytes total. |
| **QoS 0/1/2** | Choose delivery guarantee per message. |
| **Retained messages** | Last-known-value caching built into the broker. |
| **Last Will & Testament** | Automatic "device died" notifications. |
| **Topic wildcards** | `home/+/temp`, `home/#` — hierarchical subscriptions. |
| **Extremely low battery/bandwidth** | Designed for 9600 baud modems and coin-cell devices. |

### When To Use It

- **IoT fleets** — smart meters, sensors, factory equipment.
- **Merchant / point-of-sale devices** — Paytm Soundbox, card terminals.
- **Fleet / asset tracking** — vehicles, delivery riders.
- **Mobile push to constrained devices** — anything on flaky cellular.
- **Home automation** — MQTT is the lingua franca of smart-home hubs.

**Don't use for:** browser-facing real-time (no native browser support; use WebSocket), large payloads (designed for small messages), or request/response RPC patterns.

### Real Apps Using It

- **Paytm Soundbox** ([paytm.md](../apps/paytm.md)) — ~35M merchant devices each hold a persistent MQTT (or long-poll) connection to Paytm's broker. On `TXN_SUCCESS`, the notification service publishes; the device plays the TTS clip "₹150 received." Crucial choice: MQTT over polling because 35M devices polling would melt the network and drain batteries.
- **Ola** ([ola.md](../apps/ola.md)) — Tech stack includes **EMQTT** (EMQX, an MQTT broker) for driver/device connections.
- **WhatsApp** ([whatsapp.md](../apps/whatsapp.md)) — Early WhatsApp used an MQTT-like protocol over long-lived sockets for message push; the architecture still looks MQTT-shaped (connection servers, fan-out, offline queue + push).

---

## 6. HTTP/2

### What It Is (Analogy)

**A multi-lane highway instead of a single-lane road.** HTTP/1.1 is one lane — one car (request) at a time per lane; to get throughput you open many separate roads (TCP connections). HTTP/2 is a highway: one road, many lanes, all the cars moving at once, with a toll system that remembers your fast-pass so you don't repeat paperwork every time.

HTTP/2 (2015) fixed the biggest inefficiencies of HTTP/1.1 without changing HTTP semantics (methods, status codes, headers — all the same).

### How It Works

HTTP/2 runs over a single TCP connection per origin and splits traffic into independent **streams**.

```
HTTP/1.1 (the old way):
  Browser opens 6 separate TCP connections to load 6 images.
  Each request blocks until its response arrives (head-of-line blocking).

  Browser ──conn1──► img1 ──► (wait)
  Browser ──conn2──► img2 ──► (wait)
  ... (6 connections, each handshakes TCP + TLS separately)

HTTP/2 (the new way):
  Browser opens ONE TCP connection. Sends all requests as streams.

  Browser ═════════════════════════════════► Server (one connection)
         stream 1: GET /img1 ────► resp
         stream 3: GET /img2 ────► resp
         stream 5: GET /img3 ────► resp
         stream 7: POST /api  ────► resp
  All interleaved on the same wire, concurrently, in either direction.
```

### Key Features

| Feature | What it means |
|---|---|
| **Multiplexing** | Many concurrent requests/responses over one TCP connection. No head-of-line blocking at HTTP level. |
| **Binary framing** | HTTP/2 is binary, not text. Smaller, faster to parse, less error-prone. |
| **Header compression (HPACK)** | Headers sent once; subsequent requests send only the diff. Static + dynamic Huffman tables. |
| **Server push** (deprecated-ish) | Server can push resources proactively (e.g., CSS along with HTML). Largely abandoned by browsers. |
| **Stream prioritization** | Client hints which streams matter most (e.g., CSS before images). |
| **Single TCP connection** | Less socket overhead, better TCP congestion-window utilization. |

**Header compression example:**
```
HTTP/1.1 request headers (~800 bytes, sent every time):
  Host: api.example.com
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...
  Accept: text/html,application/xhtml+xml,...
  Accept-Language: en-US,en;q=0.9
  Cookie: session=abc123; csrf=xyz789; ...

HTTP/2: first request sends these. Second request sends only:
  :path: /page2      (a few bytes — everything else is cached by HPACK)
```

### When To Use It

- **Modern web APIs** — nearly all CDNs, load balancers, and browsers default to HTTP/2 today.
- **Mobile app backends** — multiplexing dramatically speeds up apps that fire many API calls.
- **As the transport for gRPC** — gRPC *is* HTTP/2 + protobuf.
- **API gateways** — terminate HTTP/2 from clients, speak it to backends.

You usually get HTTP/2 for free by using any modern web server (nginx, Envoy, h2o) with TLS enabled. It's the default; you'd have to actively disable it.

### Real Apps Using It

- **Facebook** ([facebook.md](../apps/facebook.md)) — Runs HTTP/3 (HTTP/2's successor) at the edge; internally Proxygen handles HPACK and HTTP/2/3.
- **Twitter/X** ([twitter.md](../apps/twitter.md)) — External clients speak HTTPS/JSON over HTTP/2.
- **Every CDN-fronted app** in this atlas benefits from HTTP/2 multiplexing for asset loading.

---

## 7. QUIC & HTTP/3

### What It Is (Analogy)

**Sending multiple sealed envelopes through a pneumatic tube, where each envelope is independent.** If one envelope gets stuck, the others keep flowing. And the tube remembers your ID, so reconnecting after a hiccup takes zero round-trips — you're already through the door.

QUIC (Quick UDP Internet Connections, by Google, now RFC 9000) is built on **UDP**, not TCP. It moves the reliability, congestion control, and encryption *into the application layer* where it's faster to evolve. HTTP/3 is HTTP-over-QUIC.

### Why Not Just More TCP?

TCP has a fundamental problem: **head-of-line blocking at the TCP layer**. If packet #3 is lost, TCP waits to deliver packets #4, #5, #6 (which already arrived) until it reassembles #3. In HTTP/2, one lost packet stalls *all* multiplexed streams.

```
HTTP/2 over TCP:
  Stream A: ████░░░░░░░░  ← packet lost, stalls
  Stream B: ████████████  ← also stalls (waiting on A's lost packet)
  Stream C: ████████████  ← also stalls
  (one loss blocks everything — TCP head-of-line blocking)

QUIC (HTTP/3):
  Stream A: ████░░░░░░░░  ← packet lost, this stream waits
  Stream B: ████████████  ← KEEPS FLOWING (independent streams)
  Stream C: ████████████  ← KEEPS FLOWING
  (each stream has its own reliability — no cross-stream blocking)
```

### How It Works (0-RTT Magic)

```
Traditional TLS over TCP — 3 round trips before first byte:

  Client ──TCP SYN──► Server
  Client ◄──SYN-ACK── Server
  Client ──TCP ACK + TLS ClientHello──► Server          (RTT 1)
  Client ◄──TLS ServerHello + Cert + Finished─────────── Server
  Client ──TLS Finished + first request──► Server       (RTT 2)
  Client ◄──first response── Server                     (RTT 3)
  Total: ~3 round trips. On a 100ms connection = 300ms before data.

QUIC — combines transport + crypto handshake, and remembers clients:

  First visit (1-RTT):
  Client ──combined handshake + request──► Server       (RTT 1)
  Client ◄──response + handshake────────── Server

  Return visit (0-RTT!):
  Client ──"I've been here before" + request──► Server   (RTT 0)
  Client ◄──response────────────────────────── Server
  First byte of data leaves with the very first packet.
```

### Key Features

| Feature | What it means |
|---|---|
| **UDP-based** | No kernel TCP overhead; reliability implemented in user-space (faster to iterate). |
| **0-RTT resume** | Returning clients send data in their first packet. |
| **No head-of-line blocking** | Independent stream-level reliability; one loss doesn't stall others. |
| **Connection migration** | Switch from WiFi to cellular without dropping the connection (identified by connection ID, not IP:port). |
| **Encryption mandatory** | TLS 1.3 baked in. There is no unencrypted QUIC. |
| **Faster handshake** | 1-RTT initial, 0-RTT resume — critical for mobile/flaky networks. |

### When To Use It

- **Modern web serving** — HTTP/3 is now supported by Chrome, Safari, Firefox, Cloudflare, Akamai, Google, Facebook. Enable it at the CDN/edge and clients automatically upgrade.
- **Mobile-heavy apps** — connection migration + 0-RTT are huge wins on flaky cellular.
- **Low-latency first-byte** — ad bidding, real-time APIs.

**Don't actively "build on QUIC"** unless you're doing something specialized — let your CDN/LB handle it. The win is automatic for end users.

### Real Apps Using It

- **Google (Search, YouTube)** — Pioneered QUIC; YouTube and Search default to it for Chrome users.
- **Facebook** ([facebook.md](../apps/facebook.md)) — HTTP/3 at the edge for TLS termination and media delivery.
- **YouTube/TikTok** — Video segment fetches over HTTP/3 reduce buffering on mobile.
- **TikTok** ([tiktok.md](../apps/tiktok.md)) — Client→edge uses HTTPS/QUIC for video delivery.

---

## 8. Long Polling

### What It Is (Analogy)

**A persistent kid in the back seat.** "Are we there yet?" "No." "Are we there yet?" "No." ... until finally "Yes!" Each question is a fresh HTTP request, and the server *deliberately holds the response open* until it has something to say (or times out). It's the hack we used before WebSocket existed.

### How It Works

```
LEGENDARY HACK (circa 2000–2010):

  Client ──GET /updates──► Server
  Server: (holds the request open, waiting for an event...)
  ...5 seconds pass...
  Server: (event arrives!) ──200 OK + payload──► Client

  Client immediately:
  Client ──GET /updates──► Server      (re-request right away)
  Server: (holds again...)
  ...30 seconds (timeout, no event)...
  Server ──204 No Content──► Client    (empty response, "try again")

  Client ──GET /updates──► Server      (re-request)
  ...
```

**The cost:** every "are we there yet" is a full HTTP round trip (TCP keepalive helps, but still). Each held request consumes a server thread/connection. Under load, this is dramatically worse than WebSocket.

### Key Features / Tradeoffs

| Feature | What it means |
|---|---|
| **Works on HTTP/1.1** | No special protocol support needed — pure HTTP. Works through every proxy ever made. |
| **Legacy-friendly** | Old browsers (IE6 era) that can't do WebSocket. |
| **High overhead** | Each cycle = full request/response. TCP/TLS cost repeats if keepalive fails. |
| **Latency floor** | Even in best case, each event triggers a new request cycle. |
| **Server thread/connection pressure** | Holding N connections means N server resources occupied. |

### When To Use It

- **Only as a fallback** when WebSocket/SSE are unavailable (very old browsers, locked-down corporate proxies that block Upgrade).
- **Legacy systems** you can't change.
- **Quick prototypes** where you don't want to set up WebSocket infra.

**Modern rule:** If you're considering long polling in 2026, you almost certainly want SSE (simpler, auto-reconnect) or WebSocket (bidirectional). Long polling is included here because (a) legacy codebases still have it, (b) several apps in this atlas historically used it, and (c) understanding it clarifies *why* WebSocket and SSE exist.

### Real Apps Using It

- **Paytm Soundbox** ([paytm.md](../apps/paytm.md)) — Documented as a *fallback*: the device holds "MQTT or long-poll" connections. MQTT is preferred; long-poll is the degrade path for networks/devices where MQTT can't establish.
- **Early Gmail Chat / Facebook Chat** — Before WebSocket, these used long polling / Comet. Gmail's chat famously held requests open for ~30s.
- **Early Twitter live feed** — Comet-style long polling before migrating to WebSocket/SSE.

---

## 9. XMPP (Jabber)

### What It Is (Analogy)

**The postal service, but for chat.** Everyone has a permanent mailbox address (`alice@jabber.org`), letters are XML envelopes routed between servers, and the system is federated — Gmail can send to Yahoo because they agree on the envelope format. XMPP (Extensible Messaging and Presence Protocol), formerly Jabber, is a federated, XML-based messaging protocol standardized in RFC 6120/6121.

### How It Works

XMPP is **federated** — like email. Your server talks to my server; users on different servers can chat.

```
    Alice's client         Bob's client
         │                      │
         ▼                      ▼
    ┌──────────┐           ┌──────────┐
    │ alice.org│◄──S2S────►│ bob.com  │     (Server-to-Server, federated)
    │ XMPP srv │  dialback │ XMPP srv │      over TCP/TLS, port 5269
    └──────────┘           └──────────┘
         ▲                      ▲
         │                      │
    (C2S over TCP/TLS, port 5222)
```

**Stanzas (the XML envelopes):**

```xml
<!-- A message -->
<message to="bob@bob.com" from="alice@alice.org" type="chat">
  <body>Hey Bob!</body>
</message>

<!-- Presence: "I'm online" -->
<presence>
  <show>chat</show>
  <status>Available</status>
</presence>

<!-- IQ: Info/Query (request/response RPC-style) -->
<iq type="get" to="bob@bob.com">
  <query xmlns="jabber:iq:roster"/>
</iq>
```

**Connection lifecycle:**
```
Client ──TCP connect──► Server (port 5222)
Client ──<stream>──► Server          (open XML stream)
Client ◄──<stream>── Server
Client ──TLS upgrade (STARTTLS)──►
Client ──SASL authenticate──►
Client ──<bind> (bind resource)──►   (gets alice@alice.org/phone)
Client ──<presence/>──►              (announce online)
... (send/receive <message>, <presence>, <iq> stanzas) ...
Client ──</stream>──►                (close)
```

### Key Features

| Feature | What it means |
|---|---|
| **Federated** | Like email — servers interconnect. Anyone can run a server. |
| **Presence** | First-class: online/offline/away status, "last seen." |
| **XML-based** | Human-readable, very extensible via namespaces, but verbose. |
| **Extension (XEPs)** | Hundreds of extensions: file transfer, MUC (group chat), VoIP (Jingle → WebRTC), PubSub. |
| **Persistent connections** | Like WebSocket — long-lived TCP socket per client. |
| **Asynchronous** | Server pushes stanzas to client at any time. |

### The Big Tradeoff: XML Verbosity

XMPP's XML is readable but **heavy**. A presence update can be 200+ bytes of XML; the equivalent in a binary protocol is 10 bytes. At massive scale (hundreds of millions of concurrent connections), this bandwidth and parsing cost adds up. This is why WhatsApp moved *away* from raw XMPP.

### When To Use It

- **Federated chat systems** (rare today — most modern chat is siloed).
- **Interoperable messaging** (the original Jabber vision).
- **Extensible real-time systems** needing pub/sub, presence, and RPC in one protocol.
- **Legacy integration** with existing XMPP infrastructure (Jabber, Openfire, ejabberd).

**Don't use for:** new greenfield chat at scale — modern stacks use WebSocket + custom JSON/protobuf (lighter, faster, cheaper). XMPP is historically important and conceptually foundational, but rarely the right new-build choice.

### Real Apps Using It

- **WhatsApp (original)** — Famously built on a **customized XMPP** stack running on Erlang/OTP + ejabberd. They modified XMPP heavily (binary serialization, custom compression) to scale to hundreds of millions of connections. Over time they moved to a more custom protocol, but the architecture — connection servers, presence, offline message queue — is XMPP-shaped. See [whatsapp.md](../apps/whatsapp.md).
- **Google Talk / Hangouts** — Originally XMPP-federated; Google later dropped federation but the roots were XMPP.
- **Facebook Chat (early)** — Used an XMPP-derived internal protocol.
- **Jitsi Meet** — Signaling still uses XMPP (Prosody / Openfire) under the hood, with WebRTC for media.
- **Many enterprise IM systems** — Cisco Jabber, Openfire-based deployments.

---

## Comparison Table

| Protocol | Transport | Direction | Latency | Best For | Real Example |
|---|---|---|---|---|---|
| **WebSocket** | TCP | Bidirectional (full-duplex) | Very low (after connect) | Chat, real-time dashboards, collaborative editing | WhatsApp, Ola driver push |
| **SSE** | HTTP/1.1+ | One-way (server→client) | Low | Notifications, live feeds, LLM streaming | Twitter notifications, ChatGPT |
| **gRPC** | HTTP/2 | Bidirectional (req/resp + streaming) | Low (internal) | Service-to-service calls, polyglot systems | Spotify, Netflix internals |
| **WebRTC** | UDP (SRTP) | Peer-to-peer bidirectional | Ultra-low (<100ms) | Video/voice calls, P2P data | Zoom, WhatsApp calls |
| **MQTT** | TCP | Pub/sub (many-to-many) | Low | IoT fleets, constrained devices | Paytm Soundbox, Ola |
| **HTTP/2** | TCP | Request/response (+ push) | Medium | Modern web APIs, gRPC transport | All modern CDNs/apps |
| **QUIC/HTTP/3** | UDP | Request/response | Low (0-RTT) | Mobile web, low-latency APIs | Google, Facebook edge |
| **Long Polling** | HTTP/1.1 | Simulated push | High | Legacy fallback only | Early Gmail chat |
| **XMPP** | TCP | Bidirectional (federated) | Low | Federated chat, presence | WhatsApp (original), Jitsi |

### Latency Cheat Sheet (typical)

```
First-byte latency for a "push" event, best case:

  Long Polling:    100–500ms   (new request cycle each time)
  SSE:             10–50ms     (already-open stream)
  WebSocket:       1–10ms      (open pipe, just write a frame)
  gRPC unary:      5–20ms      (HTTP/2 multiplexed, protobuf)
  MQTT QoS 1:      5–30ms      (broker hop + PUBACK)
  WebRTC media:    20–80ms     (P2P UDP, one-way)
  QUIC 0-RTT:      0ms extra   (data in first packet)
```

---

## How To Choose — Decision Matrix

```
┌────────────────────────────────────────────────────────────────┐
│  "I need the server to push updates to a browser"              │
│    ├─ One-way only, simple    → SSE                            │
│    └─ Bidirectional           → WebSocket                      │
│                                                                │
│  "Two services need to talk to each other"                     │
│    └─ gRPC                                                     │
│                                                                │
│  "Millions of IoT devices need to receive tiny commands"       │
│    └─ MQTT                                                     │
│                                                                │
│  "I'm building video calls"                                    │
│    └─ WebRTC (+ signaling over WebSocket)                      │
│                                                                │
│  "I want my public API to be as fast as possible on mobile"    │
│    └─ HTTP/3 (let your CDN handle it)                         │
│                                                                │
│  "I'm stuck on IE6 / a proxy blocks WebSocket"                 │
│    └─ Long Polling (last resort)                               │
│                                                                │
│  "I need federated chat across independent servers"            │
│    └─ XMPP (or just don't federate and use WebSocket)          │
└────────────────────────────────────────────────────────────────┘
```

---

## The Universal Pattern: Connection Servers

Across nearly every real-time app in this atlas, the same architectural pattern shows up regardless of the underlying protocol:

```
                 ┌─────────────────────────────────────────┐
                 │            STATELESS SERVICES            │
                 │   (Dispatch, Feed, Notification, etc.)   │
                 └──────────────────┬──────────────────────┘
                                    │  publish message
                                    ▼
                 ┌─────────────────────────────────────────┐
                 │   MESSAGE ROUTER / BROKER               │
                 │   "which server holds user X's socket?" │
                 └────┬──────────┬──────────┬──────────────┘
                      │          │          │
                      ▼          ▼          ▼
                 ┌─────────┐ ┌─────────┐ ┌─────────┐
                 │ Conn    │ │ Conn    │ │ Conn    │   ◄── each holds
                 │ Server 1│ │ Server 2│ │ Server N│       N thousand
                 │ (Erlang,│ │         │ │         │       open sockets
                 │  Node,  │ │         │ │         │
                 │  Go)    │ │         │ │         │
                 └────┬────┘ └────┬────┘ └────┬────┘
                      │          │          │
                      ▼          ▼          ▼
                    (millions of persistent client sockets)

  This is how WhatsApp, Ola, Paytm, Instagram all structure real-time.
  The protocol on the socket may be WebSocket, MQTT, or custom —
  the PATTERN is identical: connection servers + router + presence.
```

This is why [whatsapp.md](../apps/whatsapp.md), [ola.md](../apps/ola.md), [paytm.md](../apps/paytm.md), [zoom.md](../apps/zoom.md), and [instagram.md](../apps/instagram.md) all describe near-identical "connection layer → routing layer → presence" architectures. The protocol choice (WebSocket vs MQTT vs WebRTC) is a detail layered on top of this universal shape.

---

## Common Interview Questions

**Q: WebSocket vs SSE — when do I pick which?**
A: If the client only needs to *receive* (notifications, live feed, LLM streaming), pick SSE — simpler, auto-reconnects, pure HTTP, works through every proxy. If the client must also *send* (chat, typing indicators, game input), pick WebSocket. SSE + a normal POST for the uplink is a valid middle ground for light bidirectional needs.

**Q: Why does Paytm use MQTT for the Soundbox instead of HTTP polling?**
A: ~35M devices polling every few seconds would generate billions of wasted requests, drain device batteries, and saturate cellular bandwidth. MQTT holds one persistent connection per device; the server pushes only when a payment arrives. QoS 1 guarantees the "₹150 received" announcement gets delivered. See [paytm.md](../apps/paytm.md).

**Q: What's the difference between HTTP/2 and HTTP/3?**
A: Transport. HTTP/2 runs over TCP — one loss stalls all multiplexed streams (TCP head-of-line blocking). HTTP/3 runs over QUIC (UDP) — each stream has independent reliability, plus 0-RTT resume and connection migration. HTTP/3 is strictly better for mobile/flaky networks; HTTP/2 is fine for stable connections.

**Q: Why doesn't WebRTC scale to big group calls P2P?**
A: In a mesh, each of N participants uploads N-1 video streams. Bandwidth is O(N²) and each participant's upload saturates fast. Beyond ~4 people you need an SFU (Selective Forwarding Unit) — a media server that receives each stream once and forwards selectively. Zoom's MMR servers are an SFU. See [zoom.md](../apps/zoom.md).

**Q: gRPC vs REST — when?**
A: gRPC for internal service-to-service (typed contracts, protobuf efficiency, streaming). REST/JSON for browser-facing public APIs and anywhere humans need to debug raw requests. They coexist: public REST gateway → internal gRPC mesh is the standard pattern.

**Q: Why did WhatsApp move away from pure XMPP?**
A: XML verbosity. At hundreds of millions of concurrent connections, the bandwidth and CPU cost of XML parsing became prohibitive. They kept the XMPP-shaped architecture (connection servers, presence, federation concepts) but moved to more compact binary serialization over long-lived sockets. See [whatsapp.md](../apps/whatsapp.md).

**Q: What's the cost of holding millions of WebSocket connections?**
A: RAM. Each open socket needs buffers + TLS state + connection state — roughly 20–50KB per connection. 1M connections ≈ 20–50GB just for socket state, before any message handling. This is why WhatsApp chose Erlang (each connection is a ~2KB green-thread process) and why connection-server architecture matters enormously.

---

## Further Reading

- **[whatsapp.md](../apps/whatsapp.md)** — Real-time messaging at 2B users: WebSocket-like sockets, Erlang connection servers, presence, offline queue.
- **[zoom.md](../apps/zoom.md)** — Video conferencing: WebRTC principles, SFU (MMR) architecture, signaling over WebSocket.
- **[paytm.md](../apps/paytm.md)** — MQTT at 35M-device scale: Soundbox push, QoS, broker sharding.
- **[ola.md](../apps/ola.md)** — WebSocket fan-out for driver dispatch, live GPS streaming.
- **[spotify.md](../apps/spotify.md)** — gRPC internally, WebSocket for collaborative playlists.
- **[cdn.md](../concepts/cdn.md)** — HTTP/2/3 at the edge, adaptive bitrate video delivery.
- **[microservices.md](../concepts/microservices.md)** — gRPC vs async messaging for service-to-service communication.

---

> **The one-sentence summary:** every real-time system in this atlas is a variation on "hold N persistent connections, route messages between them, and choose the wire protocol that matches your latency/bandwidth/reliability/battery constraints."

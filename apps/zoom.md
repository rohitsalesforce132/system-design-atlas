# Zoom — System Design Atlas

> **One-line summary:** Zoom is a real-time video conferencing platform that connects hundreds of
> participants in a live call using a Selective Forwarding Unit (SFU) architecture — each participant
> sends their audio/video once to a media server, which selectively forwards streams to others,
> scaling to far more people than a pure peer-to-peer mesh could ever handle.

---

## 1. Overview & Scale Numbers

Real-time video is one of the hardest problems in software. You're moving large volumes of data
(audio + video frames) between many endpoints **with strict latency bounds** — a 200ms delay makes
conversation feel unnatural, and packet loss causes frozen video or robotic audio. Unlike Netflix,
which optimizes for throughput, Zoom optimizes for **low, predictable latency**.

### The numbers

| Metric                                      | Approximate value          | Why it matters                                         |
| ------------------------------------------- | -------------------------- | ------------------------------------------------------- |
| Daily meeting participants                  | ~300M+                     | Massive concurrent load                                 |
| Peak concurrent meeting participants        | ~30M+ (2024, post-pandemic norm) | The pandemic peak was far higher                  |
| Meetings per day                            | ~3.5B+ minutes of meetings | Volume is enormous                                      |
| Max participants per meeting                | 1,000 (standard) / 10,000+ (webinar) | A pure P2P mesh can't do this                  |
| Target one-way audio latency                | <150ms                     | Beyond this, conversation breaks down                   |
| Target video latency                        | <300–500ms                 | Smooth motion; small delays acceptable                  |
| Video bitrates                              | 100kbps (low) – 3+ Mbps (HD) | Adaptive to each participant's bandwidth             |
| Data per participant per hour               | ~0.5–2 GB                  | Multiplied by participants and duration                 |
| Countries served                            | 180+                       | Global media server footprint needed                   |
| Reliability target                          | 99.9%+                     | Calls must not drop                                     |

### The product goal

A host clicks "Start Meeting." Within seconds, participants click a link and join. Everyone can
see and hear each other with low latency. The host can mute, share screen, record, and manage
participants. When one person has bad bandwidth, only *their* video degrades — not the whole
call. The meeting just works, on any device, on any network.

---

## 2. High-Level Architecture

The central architectural choice for any video conferencing system is **how media flows between
participants**. There are three options:

### 2.1 The three media-flow topologies

**Option A: Peer-to-peer mesh**

```
   Each participant sends media directly to every other participant.

       A ──────── B
       │ ╲      ╱ │
       │   ╲  ╱   │
       │    ╳     │      N people → N×(N-1) streams
       │   ╱  ╲   │      e.g., 10 people = 90 streams
       │ ╱      ╲ │      Only works for ~3–5 people
       C ──────── D
```
- **Pros:** No central server; low infrastructure cost; minimal latency for small calls.
- **Cons:** Upload bandwidth scales as O(N) per participant — untenable past ~5 people. NAT
  traversal is painful.

**Option B: Centralized server / MCU (Multipoint Control Unit)**

```
   Each participant sends to a server; the server DECODES everything,
   composites a single video (e.g., "gallery view"), ENCODES it, sends back.

   A ─┐
   B ─┼─▶ [ SERVER decodes + composites + encodes ] ─▶ one stream to each
   C ─┤
   D ─┘
```
- **Pros:** Each participant sends/receives only one stream. Low client bandwidth.
- **Cons:** Server does heavy encode/decode (expensive CPU). Single point of failure. Hard to
  scale.

**Option C: SFU (Selective Forwarding Unit) — what Zoom uses**

```
   Each participant sends ONCE to the SFU.
   The SFU forwards (routes) streams to other participants WITHOUT decoding.

   A ─┐
   B ─┼─▶ [ SFU: just routes packets ] ─▶ each participant receives N-1 streams
   C ─┤                                  (at the bitrate they can handle)
   D ─┘
```
- **Pros:** Client upload is O(1) (send once). SFU is cheap (no decode). Each receiver can get
  different bitrates/resolutions (Simulcast/SVC).
- **Cons:** More moving parts than P2P. SFU is still a bottleneck and must scale.

### 2.2 Zoom's high-level architecture

Zoom splits cleanly into two planes, just like Netflix:

1. **Control plane** — meeting scheduling, auth, contacts, chat history, billing. Standard
   request/response microservices. Latency-tolerant (seconds).
2. **Media plane** — the real-time audio/video routers (SFUs) that move packets. Latency-critical
   (milliseconds).

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                        USER DEVICES                                │
   │   Desktop (Win/Mac/Linux)  |  Mobile  |  Web  |  Room system       │
   └─────────────────┬───────────────────────────┬──────────────────────┘
                     │                            │
                     │ HTTPS (signaling)          │ UDP/DTLS-SRTP (media)
                     ▼                            ▼
   ┌──────────────────────────────────┐   ┌──────────────────────────────┐
   │      CONTROL PLANE                │   │       MEDIA PLANE            │
   │                                   │   │                              │
   │  ┌─────────────┐ ┌────────────┐   │   │   ┌──────────────────────┐   │
   │  │  Web Portal │ │ Meeting    │   │   │   │   Multi-port SFU     │   │
   │  │  (scheduling│ │  Service   │   │   │   │   (Zoom MMR)         │   │
   │  │  accounts)  │ │ (session   │   │   │   │                      │   │
   │  └─────────────┘ │  mgmt)     │   │   │   │  Routes A/V packets  │   │
   │                  └────────────┘   │   │   │  between participants │   │
   │  ┌─────────────┐ ┌────────────┐   │   │   │  No decode/transcode │   │
   │  │   Auth /    │ │  Chat /    │   │   │   │  (mostly)            │   │
   │  │   Identity  │ │  Recording │   │   │   │                      │   │
   │  └─────────────┘ └────────────┘   │   │   └──────────┬───────────┘   │
   │                                   │   │              │               │
   │  Databases, queues, etc.          │   │   (clustered, geo-distributed)│
   └───────────────────────────────────┘   └──────────────┬──────────────┘
                                                            │
                                          signaling tells clients
                                          which SFU to connect to
```

### 2.3 Why an SFU is the sweet spot

The SFU is the magic that lets Zoom scale to 1,000 participants. Each participant uploads their
audio/video **once** to the SFU. The SFU forwards copies to everyone else. No participant needs
the upload bandwidth to send to 999 peers.

```
   WITHOUT SFU (mesh, 4 people):
       A sends 3 streams, B sends 3, C sends 3, D sends 3  → 12 total streams
       At 10 people: 90 streams. Dead.

   WITH SFU (4 people):
       A sends 1, B sends 1, C sends 1, D sends 1  → 4 uploads
       SFU forwards: each receives 3.  Total streams still scale, but
       each CLIENT only uploads once.
```

---

## 3. Detailed Component Breakdown

### 3.1 Client applications

Zoom's client is the unsung hero. It must:
- Capture camera + microphone (and screen for sharing).
- Encode audio/video with hardware acceleration where available.
- Implement **network adaptive bitrate** — reduce resolution/framerate when bandwidth drops.
- Handle packet loss (jitter buffer, FEC, error concealment).
- Render up to 49 video tiles in gallery view efficiently (GPU compositing).
- Do echo cancellation, noise suppression, automatic gain control on audio.

The client implements the **codec pipeline**:

```
   camera ─▶ capture ─▶ encode (H.264/VP8/VP9/AV1) ─▶ RTP packetize ─▶ UDP
                                                                   │
   speaker ◀─ decode ◀─ jitter buffer ◀─ RTP depacketize ◀─────────┤
```

### 3.2 The SFU (Zoom calls it the MMR — Multimedia Router)

This is the core media component. The MMR:
- Receives RTP (Real-time Transport Protocol) packets from each participant.
- Decides which streams to forward to which recipients (hence "selective").
- May do **Simulcast** handling: a participant sends multiple resolutions; the SFU picks the right
  one per receiver.
- May transcode audio (mix multiple audio streams into one, or convert codecs) — but tries to
  avoid video transcoding (expensive).

```
   Participant A sends 3 simulcast layers: low / med / high
                 │
                 ▼
   ┌──────────────────────────────────┐
   │              SFU / MMR            │
   │                                   │
   │  For receiver B (good bandwidth): │
   │     forward HIGH layer            │
   │                                   │
   │  For receiver C (poor bandwidth): │
   │     forward LOW layer             │
   │                                   │
   │  For receiver D (audio only):     │
   │     forward audio only            │
   └──────────────────────────────────┘
```

**Simulcast** is what makes "one person has bad internet doesn't degrade everyone" work. Each
sender encodes multiple resolutions; the SFU picks per receiver.

### 3.3 The Meeting Service (control plane)

Manages meeting lifecycle: create meeting, generate join URL, authenticate participants, enforce
host controls (mute, lock, waiting room), track who is in the meeting, and **assign each
participant to an MMR**.

When you start a meeting, the Meeting Service decides which MMR (in which datacenter) will host
it — based on participant locations (to minimize latency), MMR capacity, and failover
considerations.

### 3.4 Signaling

Signaling is everything that isn't media: "you're muted", "new person joined", "screen share
started", "host ended meeting". Zoom uses a custom protocol over HTTPS/WebSocket/TCP. Signaling
is low-bandwidth but must be reliable and ordered (unlike media, which can tolerate loss).

```
   MEDIA  (UDP, lossy OK, latency-critical):
      "Here's video frame #1234"

   SIGNALING (TCP/WS, reliable, latency-tolerant):
      "Alice joined", "Bob raised hand", "Screen share started by Carol"
```

### 3.5 Recording service

When recording is enabled, the MMR forks a copy of each participant's media stream to a recording
pipeline, which muxes audio + video + screen share into an MP4. Recordings are stored in object
storage (S3-like) and transcribed (for Zoom's transcript feature).

### 3.6 Chat & reactions

Text chat, emoji reactions, and in-meeting file transfers are handled by a chat service over the
signaling channel (or a separate WebSocket). This is essentially a chat app layered on top of the
meeting.

### 3.7 WebRTC (the underlying transport)

Zoom historically used a **custom UDP-based protocol**, not pure WebRTC, for tighter control over
congestion, FEC, and client behavior. (Browser-based Zoom uses WebRTC.) The principles are the
same:

- **RTP** carries the media with timestamps and sequence numbers.
- **RTCP** carries control feedback (packet loss reports, bandwidth estimates).
- **DTLS-SRTP** encrypts the media.
- **STUN/TURN** handle NAT traversal (help peers/SFUs find each other across firewalls).

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────┐ hosts   ┌──────────────────┐
   │    User      │1───────*│     Meeting       │
   │ - id         │         │ - id              │
   │ - email      │         │ - host_id         │
   │ - account    │         │ - topic           │
   │ - settings   │         │ - start, duration │
   └──────────────┘         │ - passcode        │
                            │ - mmr_id (assigned│
   ┌──────────────┐ in      │   media router)   │
   │ Participant  │1───────*│ - status          │
   │ - meeting_id │         │ - recording?      │
   │ - user_id    │         └────────────────────┘
   │ - role (host/│
   │        member)         ┌──────────────────┐
   │ - joined_at  │         │   MMR / Server   │
   │ - audio_on?  │         │ - id             │
   │ - video_on?  │         │ - region         │
   │ - hand?      │         │ - capacity       │
   └──────────────┘         │ - load           │
                            │ - meetings[]     │
                            └──────────────────┘

   ┌──────────────┐ sent in ┌──────────────────┐
   │   Message    │1───────*│   Participant    │
   │ - id         │         └──────────────────┘
   │ - meeting_id │
   │ - sender     │         ┌──────────────────┐
   │ - text       │         │   Recording      │
   │ - sent_at    │         │ - id, meeting_id │
   └──────────────┘         │ - url (S3)       │
                            │ - duration       │
                            └──────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                          | Why                                  |
| ------------------------------- | ------------------------------ | ------------------------------------ |
| Users, accounts, settings       | MySQL/PostgreSQL (sharded)     | Transactional, consistent            |
| Meeting metadata                | MySQL + Redis (active meetings cache) | Fast lookup for in-progress meetings |
| Active meeting participant state| In-memory (MMR + Redis)        | Sub-second updates; ephemeral        |
| Chat messages                   | Redis (live) → DB (history)    | Low-latency in-meeting; durable later |
| Recordings                      | Object storage (S3-like)       | Large blobs, infrequent reads        |
| MMR registration / load         | Service discovery (etcd-like)  | Dynamic, health-checked              |
| Signaling                       | WebSocket connections (ephemeral) | Real-time, not stored long-term    |

### 4.3 Why meeting state is mostly in-memory

During a meeting, state changes constantly (people mute, raise hands, join, leave). Writing every
change to a database would be too slow and create hot rows. The MMR holds authoritative meeting
state in memory; only durable outcomes (the meeting ended, here's the recording, here's the
attendee list) are persisted after the meeting.

---

## 5. Request Flow — Joining a Video Call

```
PARTICIPANT     CONTROLLER     MEETING SVC     MMR (SFU)      OTHER PARTICIPANTS
   │                │               │              │                 │
   │─click link────▶│               │              │                 │
   │                │─join request─▶│              │                 │
   │                │               │─auth, check  │                 │
   │                │               │  passcode,   │                 │
   │                │               │  waiting room│                 │
   │                │               │              │                 │
   │                │               │─pick MMR─────▶│                │
   │                │               │◀─mmr addr────┤                 │
   │                │◀─mmr addr + ──┤              │                 │
   │                │  session token│              │                 │
   │                │               │              │                 │
   │  signaling:    │               │              │                 │
   │  "I want to    │─connect (WS)─────────────────▶│                │
   │   join meeting"│               │              │                 │
   │                │               │              │─notify others──▶│
   │                │               │              │  "Alice joined" │
   │                │◀─────────participant list─────┤                 │
   │                │               │              │                 │
   │  media setup (DTLS-SRTP key exchange over signaling)            │
   │                │               │              │                 │
   │═══ UDP media flow starts ═══════════════════════════════════════│
   │  ─send my A/V (RTP packets)──▶ (MMR)                              │
   │                │               │              │─forwards my A/V─▶│
   │◀──receive others' A/V──────────┤◀─────────────┤                  │
   │                │               │              │                 │
   │   adaptive bitrate: client measures bandwidth, switches simulcast layer
   │                │               │              │                 │
   │   throughout meeting: signaling events (mute, hand, chat) over WS
   │                │               │              │                 │
   │─leave meeting─▶│               │              │                 │
   │                │─teardown──────│──────────────▶│                 │
   │                │               │              │─notify others──▶│
   │                │               │              │  "Alice left"   │
   │                │               │              │                 │
   │                │               │─persist chat,│                 │
   │                │               │  attendee list│                │
```

**Step-by-step:**

1. **Click join link.** Client hits the Controller (a frontend service) with the meeting ID +
   passcode.
2. **Controller → Meeting Service.** Validates the passcode, checks waiting room / host approval,
   checks the participant's account/permissions.
3. **Meeting Service picks an MMR.** It consults service discovery for available MMRs, chooses one
   close to the participant (latency) with capacity. Returns the MMR's address + a session token.
4. **Client connects signaling to the MMR** over WebSocket. The MMR authenticates the session
   token and adds the participant to the meeting's roster.
5. **MMR notifies other participants** ("Alice joined") over their signaling channels. Existing
   participants' clients update their UI.
6. **Client receives the participant list** — who's in, their audio/video status, roles.
7. **Media setup.** Client and MMR exchange DTLS-SRTP keys (over signaling) to encrypt media.
   Client opens a UDP connection to the MMR.
8. **Media flows.**
   - Client encodes its camera + mic, packetizes into RTP, sends UDP packets to the MMR.
   - MMR forwards copies to every other participant (selecting the right simulcast layer per
     receiver).
   - Client receives others' RTP packets, runs them through jitter buffer + decoder, renders to
     screen / speakers.
9. **Adaptive bitrate.** Client continuously measures available bandwidth and round-trip time.
   It adjusts its send resolution/framerate (via simulcast layer switching) and requests
   specific layers from the MMR for receives.
10. **During the meeting**, signaling events flow over WebSocket: mute/unmute, screen share
    start/stop, chat messages, reactions, hand raises, participant joins/leaves.
11. **Leave meeting.** Client tears down the UDP media connection and the WebSocket. MMR removes
    the participant, notifies others, and (if recording) ensures the recording captures the
    final state. Meeting Service persists the attendee list and chat history.

---

## 6. Scaling Strategy

### 6.1 The SFU is the scaling unit

The MMR is the bottleneck. Each MMR can handle a finite number of participants (limited by CPU,
memory, and bandwidth). Zoom runs **thousands of MMRs** globally and assigns meetings to them
based on capacity + geography.

```
   Datacenter A (US East)        Datacenter B (EU)
   ┌──────────────────┐          ┌──────────────────┐
   │ MMR-1  MMR-2 ... │          │ MMR-1  MMR-2 ... │
   └──────────────────┘          └──────────────────┘
            ▲                             ▲
            │                             │
   US participants                 EU participants
   (lower latency)                 (lower latency)
```

### 6.2 Geo-distributed MMRs

To keep latency low, participants should connect to a nearby MMR. For meetings with global
participants, Zoom may use **cascaded MMRs**: MMRs in different regions forward media between
each other so that each participant has a short hop.

```
   US participant ──▶ US MMR ──┐
                                │ (inter-MMR link)
   EU participant ──▶ EU MMR ──┘
```

### 6.3 Simulcast / SVC for heterogeneous clients

Not everyone has the same bandwidth. Simulcast lets each sender emit multiple layers; the SFU
forwards the appropriate one per receiver. **SVC (Scalable Video Coding)** is a related technique
where a single encoded stream has layers that can be partially decoded.

### 6.4 Media over UDP, signaling over TCP

Media uses UDP because latency matters more than reliability (a lost video frame is fine; a
delayed one is worse). Signaling uses TCP/WebSocket because it must be reliable and ordered.

### 6.5 FEC and jitter buffers for lossy networks

- **Forward Error Correction (FEC):** send redundant packets so the receiver can reconstruct
  losses without retransmission (no time for retransmits).
- **Jitter buffer:** client buffers incoming packets briefly to smooth out network jitter,
  trading a little latency for smooth playback.
- **Packet loss concealment:** audio codecs predict missing samples to avoid clicks.

### 6.6 Horizontal scaling of the control plane

The Meeting Service, auth, chat, and recording services are standard stateless microservices
behind load balancers, scaled horizontally. Only the MMRs are stateful (they hold live media
sessions).

### 6.7 Graceful degradation

When bandwidth is constrained, Zoom degrades in priority order:
1. Drop video resolution / framerate first (keep audio).
2. Drop video entirely if needed (audio-only).
3. Never drop audio — conversation survives without video but not without audio.

---

## 7. Tech Stack

| Layer                       | Technology                                            |
| --------------------------- | ----------------------------------------------------- |
| Client apps                 | C++ (desktop core), Swift/Kotlin (mobile), WebRTC (web) |
| Media transport             | Custom UDP protocol + RTP/RTCP (desktop); WebRTC (web) |
| Media codecs                | H.264, H.265, VP8, VP9, AV1 (video); Opus (audio)     |
| SFU / MMR                   | Custom C/C++ multimedia router                        |
| Signaling                   | Custom protocol over WebSocket/TCP                    |
| Control plane services      | Java, Go, microservices                               |
| Databases                   | MySQL, Redis, object storage                          |
| NAT traversal               | STUN/TURN servers                                     |
| Recording / transcoding     | FFmpeg-based pipeline, GPU transcoding farms          |
| Encryption                  | DTLS-SRTP (media), TLS (signaling), AES-256           |
| Infrastructure              | Hybrid: own datacenters + cloud                       |
| Observability               | Custom telemetry, real-time QoS monitoring            |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

The easiest path today is **WebRTC + a hosted SFU**. You don't need to write an MMR from scratch.

```
   ┌────────────┐                 ┌──────────────┐                 ┌────────────┐
   │  Browser A │◀────WebRTC─────▶│   SFU server │◀────WebRTC─────▶│  Browser B │
   │  (camera)  │                 │  (mediasoup/ │                 │  (camera)  │
   │            │                 │   LiveKit/   │                 │            │
   │            │                 │   Janus)     │                 │            │
   └────────────┘                 └──────┬───────┘                 └────────────┘
                                         │
                                         │ signaling (WebSocket)
                                         ▼
                                  ┌──────────────┐
                                  │  Node.js     │
                                  │  signaling + │
                                  │  room mgmt   │
                                  └──────────────┘
```

### 8.2 Step-by-step build

1. **Pick an SFU.** Options:
   - **mediasoup** (Node.js, production-grade, used by many)
   - **LiveKit** (Go, easy SDKs, great docs)
   - **Janus** (C, very flexible)
   - **Pion** (Go, WebRTC building blocks — build your own SFU)
2. **Signaling server.** A Node.js app with `socket.io` that:
   - Creates a room.
   - When a peer joins, calls the SFU to create a transport.
   - Exchanges SDP offers/answers and ICE candidates between peer and SFU.
3. **Client.** Use the SFU's client SDK or raw WebRTC:
   ```javascript
   // Get camera
   const stream = await navigator.mediaDevices.getUserMedia({video:true, audio:true});
   // (SFU SDK handles the rest: create transport, produce, consume)
   ```
4. **Room management.** Track who's in the room; notify on join/leave; render participant tiles.
5. **UI.** A grid of `<video>` elements, one per participant. Mute buttons, screen-share button.
6. **Screen share.** `navigator.mediaDevices.getDisplayMedia()` instead of `getUserMedia()` and
   send that track to the SFU.
7. **Recording.** Many SFUs have a recording API that saves an MKV/WebM per participant; mux with
   FFmpeg.
8. **Deploy.** Run the SFU on a server with good bandwidth (e.g., a DigitalOcean droplet or EC2).
   Put a TURN server (coturn) alongside for participants behind strict NATs.

### 8.3 The simplest possible thing: pure WebRTC P2P

For a 2-person call, you don't even need an SFU. Browsers can connect directly:

```
   Browser A ◀──WebRTC (P2P, via STUN/TURN)──▶ Browser B
```

Use the `RTCPeerConnection` API directly, with a signaling channel (any way to exchange SDP
between the two — even copy/paste for a toy). This teaches you ICE, SDP, and NAT traversal.

### 8.4 What you'll learn

- Why real-time media needs UDP, not TCP.
- How an SFU scales video far beyond a P2P mesh.
- How simulcast lets heterogeneous clients coexist.
- Why audio is prioritized over video during degradation.

### 8.5 Cost for a weekend build

- A $10–20/month VPS runs mediasoup/LiveKit for a small group.
- STUN is free (Google's public STUN). TURN (coturn) needs a server with bandwidth.
- At scale, bandwidth and CPU (for transcoding) dominate cost — same as Zoom.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered        | Why Zoom chose it                                    |
| ----------------------------------------------- | ----------------------------- | ---------------------------------------------------- |
| **SFU (not mesh, not MCU)**                     | P2P mesh / MCU                | Scales beyond ~5 people; cheaper than MCU; per-receiver quality |
| **Custom UDP protocol (historically)**          | Pure WebRTC                   | Tighter control over congestion, FEC, client behavior  |
| **Simulcast / SVC**                             | Single bitrate per sender     | Heterogeneous clients; graceful degradation           |
| **Media over UDP, signaling over TCP**          | Everything over TCP           | Media tolerates loss; latency is king                 |
| **Geo-distributed MMRs**                        | Single region                 | Latency: participants need a nearby server            |
| **In-memory meeting state**                     | Database-backed state         | Sub-second updates; DB writes too slow for live state |
| **Audio prioritized over video**                | Equal priority                | Conversation survives without video, not without audio |
| **Hardware-accelerated encode/decode**          | Software-only codecs          | CPU efficiency; battery life on mobile                |

### The deepest trade-off

**Latency vs. quality.** Zoom could deliver higher video quality by buffering more (like Netflix),
but that would add seconds of latency and make real-time conversation impossible. Instead, Zoom
accepts lower, adaptive quality to keep one-way latency under ~150ms for audio. This is the
fundamental difference between *streaming* (throughput-optimized, seconds of buffer) and
*real-time conferencing* (latency-optimized, near-zero buffer). Every other decision — UDP,
SFU, simulcast, audio priority — flows from this core trade-off.

---

## 10. Common Interview Questions

**Q1: How would you design a video conferencing system like Zoom?**
Start by contrasting mesh vs. MCU vs. SFU. Explain why SFU scales: each participant uploads
once, SFU forwards. Cover the media plane (UDP/RTP) vs. control plane (TCP/WebSocket signaling).
Discuss adaptive bitrate and simulcast.

**Q2: Why not just use peer-to-peer?**
P2P mesh scales as O(N) upload bandwidth per participant. At 10 people, each uploads 9 streams —
untenable. SFU makes each participant upload O(1).

**Q3: What is an SFU and how does it differ from an MCU?**
SFU forwards media packets without decoding (cheap, selective). MCU decodes, composites, and
re-encodes one stream (expensive CPU, single output). SFU scales better; MCU uses less client
bandwidth but is server-CPU-bound.

**Q4: How do you handle participants with different bandwidth?**
Simulcast: each sender encodes multiple resolutions; SFU forwards the appropriate layer per
receiver. SVC: a single stream with decodable layers. Plus adaptive bitrate on the client.

**Q5: Why UDP instead of TCP for media?**
TCP retransmits lost packets, adding latency (head-of-line blocking). For real-time media, a lost
video frame is better dropped than delayed. UDP lets us ignore losses; FEC and jitter buffers
conceal them.

**Q6: How do you handle NAT and firewalls?**
STUN servers help peers discover their public IP. TURN servers relay media when direct UDP fails
(common in corporate firewalls). ICE orchestrates trying STUN then TURN.

**Q7: How does Zoom handle 1,000 participants?**
The SFU scales, but a single MMR has limits. Large meetings/webinars may use cascaded MMRs or a
"speaker view" optimization where only active speakers' video is forwarded to most participants
(not all 1,000 videos to all 1,000 people).

**Q8: How is media encrypted?**
DTLS-SRTP: DTLS negotiates keys; SRTP encrypts the RTP media. Signaling is over TLS. End-to-end
encryption (true E2EE) means even the SFU can't read the media — harder to achieve with an SFU
(Zoom's E2EE required special design).

**Q9: How do you keep audio latency low?**
Small audio frames (e.g., 20ms), small jitter buffer, Opus codec (low-latency, adaptive bitrate),
prioritize audio packets, drop video before audio under congestion.

**Q10: What happens if an MMR fails mid-meeting?**
The client detects the connection loss and the Meeting Service reassigns the meeting to a new
MMR. Participants reconnect. Brief disruption, but the meeting survives. MMRs are replicated and
health-checked; assignment considers failover capacity.

---

## Further reading

- WebRTC standard (w3.org) and RFCs (RFC 3550 for RTP, RFC 8722 for WebRTC security).
- mediasoup documentation (mediasoup.org) — excellent SFU reference.
- LiveKit docs (docs.livekit.io).
- "WebRTC for the Curious" (webrtcforthecurious.com) — free book.
- Zoom Engineering Blog — posts on scaling, codec choice, E2EE.
- Pion (github.com/pion/webrtc) — Go WebRTC implementation, great for learning.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

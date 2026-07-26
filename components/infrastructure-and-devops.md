# Infrastructure, DevOps, Data Processing & Monitoring

> **The invisible backbone of every app in this atlas.**
> Every WhatsApp message, every Netflix stream, every Uber pickup is moved, scheduled, transformed, and watched by the tools in this guide. Read this once and the "Tech Stack" section of every app deep dive becomes 10x clearer.

---

## How to Read This Guide

This doc has **6 sections**. They answer one question each:

| Section | Question | Examples |
|---------|----------|----------|
| **A** | How do you distribute traffic across servers? | Nginx, HAProxy, Envoy, Kong, ALB/NLB |
| **B** | How do you get content close to users globally? | CloudFront, Akamai, Cloudflare, Netflix OCA |
| **C** | How do you package and run apps on 1000s of machines? | Docker, Kubernetes |
| **D** | How do you process petabytes of data? | Hadoop, Spark, Flink, Storm, Airflow |
| **E** | How do you know things are working? | Prometheus, Grafana, Jaeger, Datadog |
| **F** | Which programming language do you pick, and why? | Erlang, Go, Java, Node.js, Python |

Each section starts with an **analogy** (the basics), then **real numbers**, then a **comparison table**, then **when to use what**.

---

# SECTION A — Load Balancers & Proxies

## Analogy First

Imagine a hospital ER. A **triage nurse** stands at the door and decides which doctor each patient sees. The nurse doesn't treat patients — she just routes them. That's a **load balancer**.

Now imagine the nurse also:
- Speaks the patient's language (translates HTTP/2 to HTTP/1) → **SSL termination**
- Checks if a doctor is in the office (health checks) → **pool management**
- Only lets each patient visit 10 times per hour → **rate limiting**
- Knows that eye patients go to Dr. A and bone patients to Dr. B → **path-based routing**

That nurse is now an **API gateway / reverse proxy**, not just a load balancer. The line between "load balancer", "reverse proxy", and "API gateway" is blurry — most modern tools do all three.

```
                    ┌──────────────────────┐
  10,000 users ────►│   Load Balancer /     │
                    │   Reverse Proxy       │
                    │   (Nginx, Envoy, ALB) │
                    └──────┬───────────────┘
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Server 1│   │Server 2│   │Server 3│
         └────────┘   └────────┘   └────────┘
```

## L4 vs L7 — The Most Important Distinction

The two "layers" come from the OSI networking model. You only need to remember two things:

- **Layer 4 (L4) = Transport layer.** The balancer sees IP addresses and port numbers only. It doesn't care if the payload is HTTP, a database query, or cat video bytes. It just shuffles TCP/UDP packets.
- **Layer 7 (L7) = Application layer.** The balancer reads the actual HTTP request — URL path, headers, cookies, host name — and can make smart decisions.

```
L4 (fast, dumb):
   User ──TCP──► LB sees src=1.2.3.4:443, dst=10.0.0.5:80 ──► forwards

L7 (smart, slower):
   User ──HTTP──► LB reads: "GET /api/users/42 HTTP/1.1"
                  routes to API service (not /static image service)
                  terminates TLS (SSL offload)
                  adds header: "X-Request-Id: abc123"
```

**Rule of thumb:** L4 is a fast traffic cop. L7 is a smart app-aware router. Modern production systems use **both** — an L4 balancer in front of an L7 layer.

```
Internet ──► [L4: AWS NLB] ──► [L7: Nginx/Envoy] ──► App instances
             (fast TCP)         (smart HTTP routing)
```

## The Tools

### Nginx

**What it is:** The most popular open-source web server + reverse proxy in the world. Pronounced "Engine-X". Started in 2002 by Igor Sysoev to solve the "C10K problem" — how to handle 10,000 concurrent connections on one box.

**How it works (event-driven):** Older servers like Apache spawn a thread per connection (10,000 connections = 10,000 threads = memory explosion). Nginx uses an **event loop** — one worker process handles thousands of connections via async I/O.

```
Apache model:         Nginx model:
                      ┌─────────────────────┐
Conn1 → [Thread]      │ Worker process      │
Conn2 → [Thread]      │  (event loop)       │
Conn3 → [Thread]      │  handles 10,000+    │
...                   │  connections async  │
Conn10K → [Thread]    └─────────────────────┘
(10K threads =        (1 process, ~10MB RAM,
 ~80GB RAM)            handles everything)
```

- **Layer:** L7 (HTTP) and L4 (TCP/UDP stream module).
- **Key features:** Reverse proxy, load balancing (round-robin, least-conn, IP-hash), SSL/TLS termination, HTTP/2 & HTTP/3 (QUIC), gzip compression, static file serving, caching, rate limiting.
- **Real number:** A single Nginx box handles **50,000+ concurrent connections** easily. Netflix serves ~30% of internet traffic through custom Nginx-based servers.
- **When to use:** Default choice for an entry-point reverse proxy, TLS termination, static file serving, and simple L7 load balancing. Startups to giants (Netflix, Dropbox) all use it.

### HAProxy

**What it is:** "High Availability Proxy" — a pure load balancer (not a web server). Written in C, legendary for raw performance and observability. Willy Tarreau created it in 2001.

- **Layer:** L4 and L7. Originally L4 king; now excellent L7 too.
- **Key features:** Ultra-low latency (sub-millisecond), detailed stats endpoint (`/haproxy?stats`), connection draining for zero-downtime deploys, stick tables for rate limiting / session tracking, native health checks with custom thresholds.
- **Real number:** HAProxy routinely handles **2,000,000+ concurrent connections** and **40 Gbps+ of TLS** on a single box. GitHub runs it as their edge L4/L7 tier.
- **When to use:** When you need maximum performance, detailed health-check logic, or a dedicated TCP load balancer. Common at the database layer (balancing MySQL/Postgres read replicas) where Nginx doesn't fit.

### Envoy

**What it is:** A modern L7 proxy built by Lyft in 2016, now the backbone of the **service mesh** movement (Istio, Consul, AWS App Mesh all use it under the hood). Written in C++.

- **Layer:** Primarily L7 (with L4 TCP proxy mode).
- **Key features:**
  - **gRPC-aware** — understands HTTP/2 streams natively (older proxies mangle them).
  - **xDS dynamic configuration** — config can be pushed live via API without reloads (huge at scale).
  - **Rich observability** — every request emits metrics + traces automatically.
  - **Service mesh sidecar** — runs next to every app container, intercepting all traffic.
- **When to use:** Service-to-service traffic in microservices (Uber, Airbnb, Slack all use it), gRPC-heavy systems, or when you need a control plane like Istio.

```
Service mesh pattern (Envoy as sidecar):

   ┌─────────────────┐        ┌─────────────────┐
   │   Pod: Orders    │        │   Pod: Payments  │
   │ ┌──────────────┐ │        │ ┌──────────────┐ │
   │ │ Orders App   │ │        │ │ Payments App │ │
   │ │  (Node.js)   │ │        │ │  (Java)      │ │
   │ └──────┬───────┘ │        │ └──────▲───────┘ │
   │        │         │        │        │         │
   │ ┌──────▼───────┐ │        │ ┌──────┴───────┐ │
   │ │   Envoy       │◄────────┼─┤   Envoy       │ │
   │ │  (sidecar)    │ mTLS+   │ │  (sidecar)    │ │
   │ │  metrics,trc  │ retries │ │               │ │
   │ └──────────────┘ │        │ └──────────────┘ │
   └─────────────────┘        └─────────────────┘
```

### Zuul

**What it is:** Netflix's open-source API gateway, written in Java. Originally Zuul 1 (synchronous, blocking) → Zuul 2 (asynchronous, non-blocking on Netty). Famous as the gateway in front of all Netflix streaming traffic.

- **Layer:** L7.
- **Key features:** Filters pipeline (pre/route/post/error filters), tight integration with NetflixOSS (Eureka service discovery, Hystrix circuit breaker, Ribbon client-side LB), dynamic routing.
- **When to use:** Mostly legacy / Netflix-stack shops. For greenfield projects, prefer Kong or Envoy. Mentioned here because Zuul still powers a meaningful chunk of internet video.

### Kong

**What it is:** An API gateway built on **Nginx + OpenResty (Lua)** (and now a newer data-plane version). Started at Mashape in 2014. Think "Stripe for API management" — plugin-driven.

- **Layer:** L7.
- **Key features:** Plugin system (auth, rate-limiting, JWT, OAuth2, logging, tracing, serverless functions via Lua), multi-DB or DB-less mode, declarative YAML config, Kong Enterprise adds a GUI + OIDC + mTLS.
- **Real number:** Kong claims **>50,000 requests/sec** on commodity hardware.
- **When to use:** When you need an API gateway with auth, rate limiting, and a plugin ecosystem fast — exposing public APIs to third-party developers.

### AWS ALB / NLB / ELB (Cloud Balancers)

Amazon offers three managed balancers; you pay per hour + per "LCU" (load balancer capacity unit). No servers to manage.

| | **ALB** (Application LB) | **NLB** (Network LB) | **GLB** (Gateway LB) |
|---|---|---|---|
| **Layer** | L7 (HTTP/HTTPS/gRPC) | L4 (TCP/UDP/TLS) | L3 (transparent) |
| **Throughput** | ~100s Gbps | Millions of conn/s, ultra-low latency | Middlebox inspection |
| **Use** | Web apps, microservices, ECS/EKS | Gaming, IoT, MQTT, extreme perf | Insert 3rd-party appliances inline |
| **Price/mo (light)** | ~$16 base + traffic | ~$16 base + $0.006/LCU-hr | ~$0.0225/hour + NLCU |

```
AWS typical stack:
   Route53 ──► CloudFront ──► ALB ──► ECS/K8s containers
                              (L7, path routing)
```

## Comparison Table — Load Balancers & Proxies

| Tool | Layer | Type | Config | Best For | Used By |
|------|-------|------|--------|----------|---------|
| **Nginx** | L4 + L7 | Reverse proxy / LB | Static files + reload | Default entry point, TLS, static files | Netflix, Dropbox, GitHub |
| **HAProxy** | L4 + L7 | Pure LB | Static + hot reload | Max perf, DB load balancing | GitHub, Reddit, StackOverflow |
| **Envoy** | L7 (L4 mode) | Sidecar / edge | xDS dynamic API | Service mesh, gRPC | Uber, Airbnb, Slack, Lyft |
| **Zuul** | L7 | API gateway | Java filters | Netflix-stack shops | Netflix |
| **Kong** | L7 | API gateway | YAML / DB | Plugins, public APIs | Inditex, Rakuten |
| **AWS ALB** | L7 | Managed | AWS console/CLI | AWS-native web apps | Airbnb, Epic Games |
| **AWS NLB** | L4 | Managed | AWS console/CLI | IoT, gaming, low-latency | Slack |

## When to Use What (Decision Tree)

```
Do you run on AWS and want zero ops?
├─ Yes → AWS ALB (web) or NLB (TCP)
└─ No → continue

Is it service-to-service (microservices)?
├─ Yes, gRPC-heavy → Envoy (or Istio)
├─ Yes, REST + plugins → Kong
└─ No → continue

Edge reverse proxy / TLS / static files?
├─ Need max perf + L4 → HAProxy
└─ Default → Nginx
```

---

# SECTION B — Content Delivery Networks (CDN)

## Analogy First

Imagine a book library. If every reader in the world had to fly to **one library in Virginia** to read a book, that's chaos. Instead, libraries **copy** popular books to branches in every city. Now readers get the book in 5 minutes, not 5 days. A CDN does this for web content — copies of your files live in hundreds of "branches" (edge locations) worldwide.

**The speed-of-light problem:** A photon in fiber from Virginia to Mumbai takes ~70ms one way. Round trip = 140ms minimum. The CDN edge in Mumbai answers in ~5ms. That's why CDNs exist.

```
WITHOUT CDN:
  Mumbai user ──150ms──► Origin (Virginia) ──150ms──► back
  (every image, every JS file, every video chunk = 300ms wait)

WITH CDN:
  Mumbai user ──5ms──► Mumbai edge (cache HIT) ──5ms──► back
  (10ms wait — 30x faster)
```

## How a CDN Works (One Last Time)

```
1. Browser asks DNS for images.netflix.com
2. DNS answers with the IP of the nearest CDN edge
3. Browser → CDN edge
4. Edge checks cache:
   ├─ HIT  → return immediately (5ms)
   └─ MISS → fetch from origin, cache, return (150ms first time, 5ms after)
```

## The Tools

### Amazon CloudFront

**What it is:** AWS's CDN, launched 2008. Deep integration with S3, EC2, Lambda@Edge, and the rest of AWS.

- **Edge locations:** ~600+ points of presence (PoPs) in 50+ countries, plus ~13 regional edge caches (a middle tier).
- **Pricing model:** Pay-as-you-go per GB transferred out + per-HTTPS-request. No commitments. **~$0.085/GB** for the first 10TB in the US (cheaper at higher tiers). Origin (S3) → CloudFront transfer is free.
- **Special features:** Lambda@Edge (run code at the edge), signed URLs/cookies for private content, Field-Level Encryption, real-time logs to Kinesis.
- **When to use:** You're already on AWS and want a no-ops CDN. Spotify (assets), Slack (file uploads), and many Netflix non-video workloads run on it.

### Akamai

**What it is:** The original CDN — founded 1998 out of MIT. The "Intelligent Edge" platform. If you've been on the internet, you've used Akamai without knowing it.

- **Edge locations:** ~4,200+ PoPs across 130+ countries — the largest in the world by reach. They literally put servers inside ISP networks to be 1 hop from users.
- **Pricing model:** Enterprise contracts, negotiated, **~$0.02–$0.05/GB** at scale. Commit-based discounts. No simple public price list.
- **Special features:** Largest global reach (great for emerging markets), web app firewall (Kona), bot mitigation, image optimization, edge compute.
- **When to use:** Truly global audience including hard-to-reach regions, large media delivery, or when you need a single vendor for CDN + security + edge compute. Used by Apple, Microsoft, Facebook's static assets.

### Cloudflare

**What it is:** Started 2009 as a "security + CDN in one click" product. Now a major edge compute platform (Workers). Famously gives away a generous free tier.

- **Edge locations:** ~330+ cities across 100+ countries, **~320 Tbps** of network capacity (claims to handle a significant chunk of global internet DDoS mitigation).
- **Pricing model:** **Free tier** (unlimited bandwidth!), Pro $20/mo, Business $200/mo, Enterprise (negotiated, often **$0.005–$0.02/GB** at high volume). Free tier includes unlimited DNS, SSL, and DDoS mitigation.
- **Special features:** Workers (serverless JS/WASM on edge, cold start ~5ms), D1 (edge SQL), R2 (S3-compatible storage with **$0 egress fees** — direct shot at AWS), zero-trust networking.
- **When to use:** Any size project — free tier for hobby to enterprise. Especially strong when you want security + CDN + edge compute from one vendor. Discord, 1Password, and a huge chunk of the indie web run on it.

### Netflix Open Connect (OCA)

**What it is:** Netflix's **purpose-built** video CDN. They didn't use a generic CDN — they built their own with custom appliances placed inside ISPs for free. This is *why* Netflix streams don't buffer while your other video apps do.

- **How it works:** Netflix gives ISPs free **Open Connect Appliances (OCAs)** — 1U/2U servers loaded with the most popular shows. The ISP gets free Netflix traffic off their backbone; Netflix gets storage one hop from users.

```
Generic CDN model:
   User ──► ISP ──► Internet transit ──► CDN edge ──► Origin

Netflix OCA model (inside the ISP):
   User ──► ISP ──► OCA (free Netflix box inside ISP)
                          │ (cache fill at night)
                          └─► Netflix origin (AWS)
```

- **Edge locations:** Thousands of OCAs inside ISPs in 100+ countries.
- **Pricing model:** Netflix pays to build and ship boxes; ISPs host them for free (mutually beneficial — saves ISP on transit costs). ~**80%** of Netflix traffic is served directly from OCAs inside the user's ISP.
- **When to use:** You can't — it's Netflix-internal. But the **lesson** is profound: at extreme scale, building your own delivery network beats renting one. YouTube (Google Global Cache) and Akamai did the same thing first.

## Comparison Table — CDNs

| CDN | PoPs | Strength | Pricing Model | Standout Feature |
|-----|------|----------|---------------|------------------|
| **CloudFront** | ~600 | AWS integration | Per GB (~$0.085) | Lambda@Edge |
| **Akamai** | ~4,200 | Global reach | Enterprise | ISP-embedded PoPs |
| **Cloudflare** | ~330 | Free tier, security | Free → Enterprise | Workers + R2 zero-egress |
| **Netflix OCA** | ~1000s+ | Video only | Netflix-owned | Custom appliances in ISPs |

## When to Use What

```
Is your content video at Netflix scale?
└─ Yes → build your own (Netflix OCA / Google GGC). Everyone else: read on.

Are you on AWS and want simple?
└─ CloudFront

Need global reach incl. developing markets?
└─ Akamai

Want security + CDN + edge compute cheap/free?
└─ Cloudflare (start free)
```

---

# SECTION C — Container Orchestration

## Analogy First

Imagine you're a **concert promoter** running 100 shows a night in 50 cities. You can't personally book each band, set up each stage, and call each venue. You need a **manager** who:

1. Books the band (schedules work)
2. Finds a stage (allocates a machine)
3. Sends the roadies to set up (provisions the container)
4. Notices if the singer is sick and replaces them (health checks + restarts)
5. Hires more bands when ticket demand spikes (auto-scaling)

That manager is **Kubernetes**. The "bands" are your app containers.

## Docker — The Shipping Container of Software

Before Docker (2013), deploying an app meant: install OS → install deps → configure env → pray it works on the server. "Works on my machine" was the #1 excuse in software.

Docker fixed this by packaging an app **+ its entire environment** into one immutable artifact called an **image**.

```
Traditional deployment:                Docker deployment:
  Server A: Node 14, deps v1            ┌────────────────────┐
  Server B: Node 18, deps v2  ←chaos    │ Image: app:v1.2.3   │
                                       │ Node 18 + deps v2   │
  Same code, different results.         │ + app code          │
                                       │ (identical everywhere)│
                                       └────────────────────┘
```

- **Image:** Read-only template (your app + OS + deps). Built from a `Dockerfile`.
- **Container:** A running instance of an image. Lightweight — uses the host kernel, no full guest OS.
- **Registry:** Where images live (Docker Hub, AWS ECR, GitHub Container Registry).

A single Linux server can run **hundreds of containers** (vs. ~10 VMs at best) because containers share the kernel. Boot time is **milliseconds**, not minutes.

```
VMs vs Containers:

   VMs                              Containers
   ┌──────────────────────┐        ┌──────────────────────┐
   │ AppA   AppB   AppC    │        │ AppA   AppB   AppC    │
   │ Libs   Libs   Libs    │        │ Libs   Libs   Libs    │
   ├──────────────────────┤        ├──────────────────────┤
   │ GuestOS GuestOS GuestOS│       │   Docker Engine       │
   ├──────────────────────┤        ├──────────────────────┤
   │   Hypervisor          │        │   Host OS             │
   ├──────────────────────┤        ├──────────────────────┤
   │   Hardware            │        │   Hardware            │
   └──────────────────────┘        └──────────────────────┘
   (Heavy: each VM has full OS)     (Light: shared kernel)
```

**Docker alone is enough for a few containers on one box.** Once you have 50+ containers across 10+ machines, you need **orchestration** — and that's Kubernetes.

## Kubernetes (K8s)

**What it is:** An open-source container orchestrator born at Google (based on their internal "Borg" system), released 2014, now run by the CNCF. It manages fleets of containers across fleets of machines.

### The Core Objects

```
┌────────────────────────────────────────────────────────────┐
│                       KUBERNETES CLUSTER                    │
│                                                            │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐ │
│   │   NODE 1       │  │   NODE 2       │  │   NODE 3       │ │
│   │  (worker VM)   │  │  (worker VM)   │  │  (worker VM)   │ │
│   │                │  │                │  │                │ │
│   │ ┌────────────┐ │  │ ┌────────────┐ │  │ ┌────────────┐ │ │
│   │ │   POD       │ │  │ │   POD       │ │  │ │   POD       │ │ │
│   │ │ ┌─────────┐ │ │  │ │ ┌─────────┐ │ │  │ │ ┌─────────┐ │ │ │
│   │ │ │Container│ │ │  │ │ │Container│ │ │  │ │ │Container│ │ │ │
│   │ │ └─────────┘ │ │  │ │ └─────────┘ │ │  │ │ └─────────┘ │ │ │
│   │ └────────────┘ │  │ └────────────┘ │  │ └────────────┘ │ │
│   └───────────────┘  └───────────────┘  └───────────────┘ │
│                                                            │
│   Control Plane (API server, scheduler, controller, etcd)  │
└────────────────────────────────────────────────────────────┘
```

**Pod** — the smallest deployable unit. Usually 1 container, sometimes 2-3 tightly coupled (app + sidecar). All containers in a pod share the same network namespace (talk via `localhost`) and lifecycle.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: orders-app
spec:
  containers:
    - name: orders
      image: myrepo/orders:v1.2.3
      ports:
        - containerPort: 8080
```

**Deployment** — declaratively manages a set of identical Pods. Says "I want 5 replicas of orders-app running, always." If a pod dies, K8s makes a new one. If you change the image, K8s does a **rolling update**.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-deployment
spec:
  replicas: 5                # ← always 5 pods running
  selector:
    matchLabels: { app: orders }
  template:
    metadata:
      labels: { app: orders }
    spec:
      containers:
        - name: orders
          image: myrepo/orders:v1.2.3
```

**Service** — a stable network identity for a group of pods. Pods come and go (their IPs change); a Service gives a fixed DNS name (`orders.default.svc.cluster.local`) that load-balances across whatever pods currently match the label.

```
   Caller ──► Service "orders" (stable IP + DNS)
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
     Pod1      Pod2      Pod3      ← pods may be recreated with new IPs
```

**Ingress** — exposes HTTP(S) routes from outside the cluster (think "Nginx config but for K8s").

**ConfigMap / Secret** — injects config and secrets into pods without rebuilding images.

**Namespace** — logical partition (e.g., `prod`, `staging`, `team-payments`).

### Auto-Scaling (The Killer Feature)

K8s scales automatically based on metrics. Three layers of scaling:

```
1. HPA (Horizontal Pod Autoscaler)
   CPU > 70%? → add more pods (scale out)
   CPU < 30%? → remove pods (scale in)

      ┌────────────────────────────────────┐
      │  orders Deployment                 │
      │  CPU spikes → 3 pods → 8 pods     │
      │  CPU drops  → 8 pods → 3 pods     │
      └────────────────────────────────────┘

2. VPA (Vertical Pod Autoscaler)
   Adjust CPU/memory *requests* for pods (less common).

3. Cluster Autoscaler
   Not enough nodes to fit the new pods? → ask AWS/GCP
   for another VM automatically.
```

**Real number:** Slack runs ~200 services across ~3,000+ pods on Kubernetes. Shopify handles Black Friday traffic by HPA auto-scaling from ~50 to ~1000+ pods in minutes.

### Why K8s Won (vs. Docker Swarm, Mesos, Nomad)

- **Portable:** Same YAML runs on AWS, GCP, Azure, on-prem.
- **Self-healing:** Restarts dead containers, reschedules evicted pods.
- **Declarative:** You say *what* you want (5 replicas), not *how* to keep it that way.
- **Ecosystem:** Huge community — Helm charts, operators, service meshes all standardize on it.

**Cost of K8s:** Steep learning curve, complex networking, etcd needs careful ops. **Don't reach for it until you have >10 services or >20 containers.** Below that, Docker Compose or a managed PaaS (AWS ECS, Google Cloud Run) is enough.

---

# SECTION D — Data Processing

## Analogy First

Imagine you run a census across 1.4 billion people. Two ways to count:

1. **Batch (Hadoop-style):** Mail forms to everyone, wait for all replies, then tally in one giant pass. Slow (hours/days) but can handle **any** volume of data. You know exactly when it's done.
2. **Streaming (Flink-style):** Each form is counted the moment it arrives. Result updates in real-time (milliseconds). Limited to processing data the moment it exists.

Modern systems use **both** — batch for historical reprocessing, streaming for live dashboards. This is the famous **Lambda architecture**.

```
                          ┌─────────────────────┐
   Raw events ────────────►│   Speed Layer       │──► Live views (~ms)
   (Kafka)                 │   (Flink / Storm)   │
                          └─────────────────────┘
                          ┌─────────────────────┐
   Raw events ────────────►│   Batch Layer       │──► Daily/accurate views
   (S3/HDFS)               │   (Spark / Hadoop)  │
                          └─────────────────────┘
                                   │
                                   ▼
                          ┌─────────────────────┐
                          │   Serving Layer     │──► User queries
                          │   (Presto/Trino)    │
                          └─────────────────────┘
```

## The Tools

### Hadoop / HDFS

**What it is:** The original "big data" stack (2006, Doug Cutting at Yahoo, named after his son's toy elephant). Three parts:

- **HDFS (Hadoop Distributed File System):** Splits huge files across many machines, replicates 3× for fault tolerance.
- **MapReduce:** The compute engine (write a `map` step + `reduce` step, Hadoop parallelizes across nodes).
- **YARN:** Resource manager that schedules jobs across the cluster.

```
A 1 TB file in HDFS:

  split into 128 MB blocks → 8,000 blocks
  Block 1   → Node A (copy), Node B (copy), Node C (copy)
  Block 2   → Node D, Node E, Node F
  ...
  (any 2 nodes can die, data survives)
```

- **When to use:** Largely **legacy now** — replaced by cloud object storage (S3/GCS) + Spark. Still runs in big on-prem banks and telcos. Netflix used HDFS heavily until moving to S3 + EMR.

### MapReduce

**What it is:** Google's 2004 paper that inspired Hadoop. A programming model: `map` transforms each record, `reduce` aggregates.

Classic example — **count word frequencies** in 1 TB of text:

```
Map:   "the cat sat"  → (the,1)(cat,1)(sat,1)
       "the cat ran"  → (the,1)(cat,1)(ran,1)

Shuffle (group by key):
       the → [1, 1]
       cat → [1, 1]
       sat → [1]
       ran → [1]

Reduce:  the → 2
         cat → 2
         sat → 1
         ran → 1
```

**Dead?** Mostly — Spark replaced it with in-memory speed (10–100× faster) and a much nicer API. You'll see it on whiteboards because interviewers love it.

### Spark

**What it is:** UC Berkeley's 2009 successor to MapReduce. The key insight: **keep data in RAM between job stages** instead of writing to disk after every step (which MapReduce did — slow).

- **Type:** Batch (Spark Core/SQL), micro-batch streaming (Spark Structured Streaming, ~100ms latency), ML (MLlib), graph (GraphX).
- **Real number:** Spark can be **100× faster** than MapReduce on in-memory workloads. Netflix, Uber, and almost every big-data shop run Spark.
- **When to use:** Default choice for large-scale batch ETL, ML feature pipelines, or "near real-time" (minutes) analytics.

### Flink

**What it is:** A true **streaming** engine (not micro-batch). Processes events one at a time with millisecond latency. Born in Berlin (2014).

- **Type:** Real streaming, exactly-once semantics, event-time processing, stateful computations.
- **Real number:** Flink can process **millions of events/sec** with **<10ms** end-to-end latency. Handles stateful windows over unbounded streams.
- **When to use:** Real-time fraud detection (Razorpay), live recommendation updates (Netflix/Uber), IoT, anything where seconds matter and you need exactly-once.

### Storm

**What it is:** Twitter's early (2011) streaming system. **Largely superseded by Flink** but still in production at Twitter and some older stacks. At-least-once (not exactly-once) by default.

- **When to use:** Don't pick it for new projects. Know it exists because legacy systems run on it.

### Presto / Trino

**What it is:** A **distributed SQL query engine** that runs ad-hoc queries across many data sources (S3, HDFS, MySQL, Kafka) without moving the data. Originally developed at Facebook (Presto), renamed Trino after a 2020 fork.

```
   Analyst writes SQL:
   SELECT user_id, COUNT(*) FROM s3.logs
   WHERE date='2026-07-01' GROUP BY user_id;

   Trino coordinator splits query into 1000s of fragments,
   sends each to a worker node near the data,
   collects results → analyst sees answer in seconds.
```

- **Type:** Interactive analytics (seconds to minutes). Not for ETL (use Spark).
- **When to use:** "I have 5 TB of logs in S3 and I want to run SQL over them right now." Companies: Netflix, Uber, LinkedIn all run massive Trino clusters.

### Airflow

**What it is:** A **workflow orchestrator** (not a compute engine). It *schedules* and *monitors* pipelines written as Python DAGs (Directed Acyclic Graphs). Created at Airbnb in 2014, now an Apache project.

```
A DAG of tasks:
   extract_user_events ─► filter_spam ─► join_with_orders ─► update_dashboard
                                       └► send_to_ml_team

Airflow runs each task on schedule (e.g., daily at 2am),
retries on failure, alerts on stuck pipelines.
```

- **Type:** Orchestration layer that calls Spark, Flink, dbt, Python, Bash — whatever.
- **When to use:** Any time you have multi-step data pipelines with dependencies. The **de facto standard** for batch data orchestration. Used by Airbnb, Slack, Robinhood, Reddit.

## Comparison Table — Data Processing

| Tool | Mode | Latency | Strength | When to Use |
|------|------|---------|----------|-------------|
| **Hadoop/HDFS** | Batch | Hours | Distributed storage + compute | Legacy on-prem |
| **MapReduce** | Batch | Hours | Simple programming model | Mostly historical |
| **Spark** | Batch (+micro-batch) | Minutes | In-memory speed, ML, SQL | Default for batch ETL/ML |
| **Flink** | Streaming | Milliseconds | True streaming, exactly-once | Real-time fraud, recs |
| **Storm** | Streaming | Milliseconds | Legacy streaming | Don't pick for new work |
| **Presto/Trino** | Interactive | Seconds | SQL across data lakes | Ad-hoc analytics |
| **Airflow** | Orchestrator | N/A | Schedules everything | Pipeline orchestration |

---

# SECTION E — Monitoring & Observability

## Analogy First

Imagine flying a plane. You need three things:

1. **Dashboard gauges** — altitude, fuel, speed. *"How is the system right now?"* → **Metrics**.
2. **Black box recorder** — every event logged. *"What exactly happened at 14:32:07?"* → **Logs**.
3. **Flight path trace** — the route the plane took from A to B. *"Where did the request get slow?"* → **Traces**.

Together, these three are called **observability**. The goal: when something breaks at 3am, you can answer "what, where, why" without guessing.

```
   THE THREE PILLARS:

   ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
   │    METRICS      │ │      LOGS       │ │     TRACES      │
   │                 │ │                 │ │                 │
   │ "99% CPU"       │ │ "User 42 login  │ │ Request 1234:   │
   │  counter/gauge  │ │  failed: bad pw"│ │                 │
   │                 │ │                 │ │ gateway 2ms     │
   │ aggregated      │ │  discrete event │ │  └ auth   45ms  │
   │ numbers         │ │  per request    │ │  └ db     120ms │ ← bottleneck!
   │                 │ │                 │ │  └ cache   3ms  │
   │ cheap, alerted  │ │ expensive to    │ │ expensive to    │
   │ on              │ │ store all       │ │ collect         │
   └─────────────────┘ └─────────────────┘ └─────────────────┘
```

## The Tools

### Prometheus

**What it is:** An open-source **metrics** database + scraper, born at SoundCloud (2012), now CNCF-graduated. The de facto metrics standard for Kubernetes.

- **Model:** Pull-based. Prometheus **scrapes** `/metrics` endpoints on your services every 15s. Stores time-series data with powerful query language (**PromQL**).
- **Metric types:** Counter (monotonic, e.g., `http_requests_total`), Gauge (goes up/down, e.g., `memory_usage_bytes`), Histogram (e.g., request latency buckets), Summary.
- **Real number:** A single Prometheus handles **millions of active series**. Pinterest runs hundreds of Prometheus instances.

```
Your app exposes:
   http_requests_total{method="GET",status="200"} 1452301
   http_request_duration_seconds_bucket{le="0.1"} 1450000
   memory_usage_bytes 824567808

Prometheus scrapes every 15s → stores as time series → alert if:
   rate(http_requests_total{status="500"}[5m]) > 10
```

- **When to use:** Always, for metrics. Default in the CNCF/K8s ecosystem.

### Grafana

**What it is:** An open-source **visualization** layer. Doesn't store data — queries Prometheus, Loki, Elasticsearch, etc., and renders dashboards.

- **Strengths:** Beautiful charts, alerts, annotations, templating, multi-source.
- **Pairing:** **Prometheus + Grafana** is the most common monitoring stack in the world. Prometheus stores; Grafana draws.
- **When to use:** Always, paired with whatever your TSDB is.

### Jaeger (tracing)

**What it is:** Open-source **distributed tracing**, born at Uber (2017), named after a German word for "hunter." CNCF graduated.

- **How it works:** Every request gets a unique **trace ID**. As the request flows through services (gateway → auth → orders → db), each hop records a **span** with timing.

```
Trace for "user places order":

gateway    ████████░░░░░░░░░░░░░░░░░░░░ 2ms   [span]
auth            ████████░░░░░░░░░░░░░░░ 45ms  [span]
orders               ██████████████████ 180ms [span] ← slow!
  └ db                     ████████████ 165ms [span] ← culprit
cache                              ████ 3ms  [span]

Without tracing, you see: "API latency P99 = 200ms" (but where?).
With tracing, you see: database query is the bottleneck.
```

- **When to use:** Any microservices architecture with >3 services. Essential for diagnosing "why is this request slow?" across a chain.

### Zipkin (tracing)

**What it is:** The original distributed tracing system, from Google's Dapper paper (2010), open-sourced by Twitter (2012). Predates Jaeger. Still widely used.

- **Difference vs Jaeger:** Older UI, simpler feature set. Jaeger has better Prometheus integration and CNCF momentum. Both speak the same OpenTracing/OpenTelemetry standards, so you can migrate.
- **When to use:** Existing Zipkin-based stacks. New projects → Jaeger (or just use OpenTelemetry + whichever backend).

### Datadog

**What it is:** A commercial **all-in-one** observability platform — metrics, logs, traces, APM, RUM (real user monitoring), synthetics, security — under one SaaS roof.

- **Pricing:** Per-host, ~$15–$34/host/mo for infra, more for APM/logs. Expensive at scale (a 1,000-host fleet = $180k–$400k+/year).
- **Strength:** One vendor, beautiful UI, 800+ integrations (AWS, K8s, Postgres, Redis...), no ops.
- **Weakness:** Vendor lock-in, costs balloon at scale, no on-prem option (mostly).
- **When to use:** When you have budget and want observability "now" without running 5 OSS tools. Many startups graduate from Datadog → OSS once bills cross ~$50k/mo.

## Comparison Table — Observability

| Tool | Pillar | Type | Pricing | Strength |
|------|--------|------|---------|----------|
| **Prometheus** | Metrics | OSS | Free (self-host) | K8s standard, PromQL |
| **Grafana** | Viz | OSS | Free (self-host) | Universal dashboards |
| **Jaeger** | Traces | OSS | Free (self-host) | Uber-born, CNCF |
| **Zipkin** | Traces | OSS | Free (self-host) | Original, Twitter |
| **Datadog** | All 3 | SaaS | $15–$34+/host/mo | All-in-one, no-ops |

## When to Use What

```
Just starting / small team?
  → Prometheus + Grafana + minimal logging. Free, enough.

Microservices + debugging slow requests?
  → Add Jaeger (tracing). Use OpenTelemetry SDK so you can
    switch backends later.

Big budget, no time to run infra?
  → Datadog (or New Relic, Honeycomb, Dynatrace).
  Watch the bill.
```

---

# SECTION F — Languages & Runtimes (with WHY)

## Analogy First

Picking a programming language is like picking a **vehicle** for a cross-country trip:

- **Python** = a bicycle. Easy to learn, gets you anywhere slowly. Great for data scripts.
- **Java** = a freight train. Heavy to start, but moves enormous loads reliably for decades.
- **Go** = a delivery van. Simple, fast, parks anywhere. Built for moving packages (microservices).
- **Node.js** = a sports car on the same engine as Chrome. Fast for I/O, single-threaded.
- **Erlang/Elixir** = a swarm of bees. Millions of tiny concurrent actors that never go down.

There is no "best" language — only the right tool for the workload and team. Here's why the giants picked what they picked.

## Erlang — Why WhatsApp Chose It

**The story:** WhatsApp reached **900M users with only ~50 engineers**. How? Erlang.

**What Erlang is:** A language + runtime built by Ericsson in 1986 for telecom switches. Designed around one obsession: **never go down**. Telephone exchanges can't reboot — a 911 system down for 5 minutes is a national emergency.

**Key superpower — the Actor model + lightweight processes:**

```
Most languages:                    Erlang:
  OS threads are heavy (~2MB each)   Processes are ~300 bytes each
  10,000 threads = ~20GB RAM         10MILLION processes on one box
                                     = ~3GB RAM

Each Erlang "process" is an independent
actor with its own mailbox. They send
messages — never share memory.
```

```
Why it matters for chat:

   User A connects ──► Erlang process #1234 (dedicated)
   User B connects ──► Erlang process #1235

   A sends message to B:
     process 1234 ──msg──► process 1235 ──► delivers to B

   If process 1234 crashes (bug in user A's handler):
     - Only user A's connection dies
     - The other 9,999,999 users are unaffected
     - Supervisor restarts process 1234 automatically
```

- **"Let it crash" philosophy:** Instead of defensive try/catch everywhere, Erlang lets processes crash and supervisors restart them. Cleaner code, fault isolation.
- **Hot code swapping:** Upgrade a running system without downtime — critical for telecom and chat.
- **Real number:** WhatsApp ran **2M+ concurrent TCP connections per server** on Erlang.
- **When to use:** Chat, telecom, anything needing millions of long-lived connections with fault tolerance. Discord uses Elixir (Erlang VM) for similar reasons.

## Go — Why Google Built It

**The story:** Google had billions of lines of C++ and Java. C++ compiled slowly (hours), Java was verbose, and both had painful dependency management. Google needed a language that compiled fast, ran fast, and was simple enough that any engineer could read it. So they built Go (Golang) in 2009.

**Key superpowers:**

1. **Goroutines** — cheap threads (~2KB stack each). Spawn 100,000 easily. Built-in channels for safe communication.
2. **Single static binary** — `go build` produces one executable with all deps baked in. No JVM, no DLL hell. Deploy by copying one file.
3. **Compilation speed** — large projects compile in seconds, not hours.
4. **Stdlib is rich** — production HTTP server in 10 lines.

```
Goroutines + channels:

   func main() {
       ch := make(chan int)
       go func() { ch <- doWork() }()   // runs concurrently
       result := <-ch                   // wait for it
   }

   // Spawn 100,000 of these? No problem — Go's scheduler handles it.
```

- **Used by:** Google (Kubernetes, Docker, Etcd, Prometheus — all written in Go!), Uber (geofence microservices rewrite from Node.js → Go), Twitch, Dropbox.
- **When to use:** Microservices, CLI tools, networking, anything where you want speed of development + speed of execution + simple deployment.

## Java — Why Enterprise Still Loves It

**The story:** "Write once, run anywhere" (1995). The JVM is one of the most battle-tested runtimes ever — 30 years of optimization.

**Why enterprises use it:**

- **JVM maturity:** Decades of GC tuning, JIT compilation, profiling tools.
- **Ecosystem:** Spring Boot, Kafka, Hadoop, Cassandra, Elasticsearch — all written in Java. If you're in big data, you're in Java.
- **Talent pool:** ~10M Java developers worldwide. Easy to hire.
- **Performance:** Modern JVMs are *fast*. JIT can match C++ for long-running services.

**The tradeoff:**
- Verbose syntax (though improving).
- Cold start is slow (~seconds) — bad for serverless.
- Memory-heavy (~hundreds of MB baseline per service).

- **Used by:** Almost every bank, every Hadoop/Spark shop, LinkedIn, Netflix (backend), Amazon (much of it).
- **When to use:** Big enterprise systems, big-data ecosystems, anything where you need a mature ecosystem and huge talent pool.

## Node.js — Why It Took Over Startups

**The story:** Ryan Dahl created Node.js in 2009 by ripping the V8 JavaScript engine out of Chrome and bolting on an event loop. Suddenly you could write servers in JavaScript.

**Why it spread:**

1. **One language, frontend + backend** — full-stack JS. Hire one developer, ship everything.
2. **Non-blocking I/O** — like Nginx, one thread handles thousands of connections via the event loop. Perfect for I/O-heavy apps.
3. **Huge npm ecosystem** — biggest package registry in the world.
4. **Fast startup** — good for serverless (Lambda).

```
Event loop (single thread, async I/O):

   req1 ─► db.query() ──┐
   req2 ─► fs.readFile()─┤  (these all run in parallel via OS)
   req3 ─► http.get() ───┘
                        │
                        ▼
                   single JS thread processes callbacks
                   as I/O completes
```

**The tradeoff:**
- **Single-threaded** — one CPU-heavy operation blocks everything. Bad for CPU-bound work (image processing, ML).
- **Callback hell** historically — fixed by async/await.
- **npm dependency chaos** — left-pad incident, supply-chain risk.

- **Used by:** Netflix (UI + some API), LinkedIn (mobile backend), Uber (early), PayPal, Trello.
- **When to use:** I/O-heavy web APIs, real-time apps (WebSockets), full-stack JS teams, serverless.

## Python — Why It Owns Data & ML

**The story:** Created 1991 by Guido van Rossum as a teaching language. Slow, dynamic, but **readable**. Then NumPy (2005) and TensorFlow (2015) happened — and Python became the lingua franca of data science.

**Why it won data/ML:**

- **Numpy/Pandas/Scikit-learn/PyTorch/TensorFlow** — the entire ML stack speaks Python.
- **Readable** — pseudocode that runs. Great for notebooks and prototyping.
- **Glue language** — Python orchestrates C/C++/Rust libraries that do the heavy lifting. Python is the steering wheel; NumPy is the engine.

**The tradeoff:**
- **Slow** — interpreted, ~10–100× slower than C for tight loops.
- **GIL (Global Interpreter Lock)** — true multithreading is hard (use multiprocessing instead).
- **Dynamic typing** — refactoring large codebases is painful (mypy helps).

- **Used by:** Google (ML), Instagram (Django, runs the largest Python deployment on earth), YouTube, Spotify (recsys), OpenAI, Anthropic.
- **When to use:** Data science, ML, scripting, APIs where developer speed > runtime speed, ML model serving.

## Comparison Table — Languages

| Language | Strength | Weakness | Famous Users | Sweet Spot |
|----------|----------|----------|--------------|------------|
| **Erlang/Elixir** | Massive concurrency, fault tolerance | Small ecosystem, niche | WhatsApp, Discord | Chat, telecom |
| **Go** | Simple, fast compile, single binary | Verbose error handling | Google, Uber, Docker | Microservices, CLIs |
| **Java** | Mature JVM, huge ecosystem | Verbose, memory-heavy | Banks, Hadoop, Netflix | Enterprise, big data |
| **Node.js** | One language FE+BE, async I/O | Single-threaded, CPU-bound bad | Netflix, LinkedIn, Uber | Web APIs, real-time |
| **Python** | ML ecosystem, readable | Slow, GIL | Google, Instagram, OpenAI | Data, ML, scripting |

## When to Use What

```
Building a chat / messaging system with millions of connections?
  → Erlang / Elixir

Building microservices / networking tools?
  → Go

Enterprise / big-data / hiring at scale?
  → Java

Web APIs / real-time / full-stack JS?
  → Node.js

Data science / ML / scripting?
  → Python
```

---

# Putting It All Together — A Modern Stack

Here's how all the pieces fit, for a hypothetical app like Uber:

```
                        User's phone
                             │
                             ▼
              ┌──────────────────────────┐
              │  Cloudflare (CDN + WAF)   │  ← Static assets, DDoS
              └────────────┬─────────────┘
                           ▼
              ┌──────────────────────────┐
              │   AWS ALB (L7 LB)         │  ← TLS termination
              └────────────┬─────────────┘
                           ▼
              ┌──────────────────────────┐
              │   Kubernetes Cluster      │
              │  ┌────────────────────┐  │
              │  │ Envoy (service mesh)│  │  ← mTLS, retries, tracing
              │  └─────────┬──────────┘  │
              │  ┌─────────▼──────────┐  │
              │  │ Go microservices:   │  │  ← routing, matching, pricing
              │  │  routing, matching, │  │
              │  │  pricing, payments  │  │
              │  └────────────────────┘  │
              └────────────┬─────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
        Postgres        Redis          Kafka
        (rides)        (cache)        (events)
                           │
                           ▼
              ┌──────────────────────────┐
              │   Spark + Flink           │  ← Batch + stream processing
              │   (surge pricing, ETL)    │
              └────────────┬─────────────┘
                           ▼
              ┌──────────────────────────┐
              │   Airflow                │  ← Orchestrates pipelines
              └────────────┬─────────────┘
                           ▼
              ┌──────────────────────────┐
              │   Trino                  │  ← SQL over data lake
              └──────────────────────────┘

   ──────── OBSERVABILITY (parallel to everything) ────────
   Prometheus (metrics) → Grafana (dashboards)
   Jaeger (traces)
   Datadog (logs + alerts) [or ELK]
```

Every component in this atlas uses **some subset** of this stack. The art of system design is picking the right subset for your scale, budget, and team.

---

## Cross-References

This guide connects to the foundational concept docs:

- **Load balancing deep dive:** [`concepts/load-balancing.md`](../concepts/load-balancing.md)
- **CDN deep dive:** [`concepts/cdn.md`](../concepts/cdn.md)
- **Microservices patterns:** [`concepts/microservices.md`](../concepts/microservices.md)
- **Caching (Redis):** [`concepts/caching.md`](../concepts/caching.md)
- **Message queues (Kafka):** [`concepts/message-queues.md`](../concepts/message-queues.md)

And to the app deep dives — each app's **Tech Stack** section names which tools from this guide they actually use in production.

---

> **TL;DR:** There's no magic in big systems — just the right combination of balancers, caches, queues, containers, data engines, and monitors, each picked for the job it does best. Learn what each tool is *for*, and the architectures of WhatsApp, Netflix, and Google start to look inevitable rather than mysterious.

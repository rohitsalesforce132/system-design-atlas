# Messaging & Streaming Technologies — The Deep Reference

> **Companion to** [`concepts/message-queues.md`](../concepts/message-queues.md). That file explains *why* you use a queue. This file is the **per-technology deep dive** — internals, delivery guarantees, ordering rules, and how the world's biggest apps actually use each broker.

---

## 📖 Table of Contents

1. [Mental Model: Two Families of Brokers](#mental-model)
2. [Apache Kafka — The Distributed Log (Deep Dive)](#kafka)
3. [RabbitMQ — The Smart Post Office](#rabbitmq)
4. [Amazon SQS (+ SNS) — The Managed Pair](#sqs)
5. [Redis Streams — The In-Memory Conveyor](#redis-streams)
6. [NATS — The Featherweight Pub/Sub](#nats)
7. [Apache Pulsar — The Streaming Cloud](#pulsar)
8. [Celery — The Task Queue for Python](#celery)
9. [Honorable Mentions (MQTT/EMQTT, Kinesis, Google Pub/Sub)](#honorable)
10. [The Master Comparison Table](#comparison-table)
11. [How to Choose — Decision Tree](#decision-tree)

---

<a name="mental-model"></a>
## 🧠 Mental Model: Two Families of Brokers

Before diving in, understand that every system below falls into one of two families. Getting this wrong leads to 90% of bad architectural decisions.

```
   FAMILY 1: MESSAGE QUEUE (Smart Post Office)      FAMILY 2: EVENT LOG (Distributed Ledger)
   ─────────────────────────────────────            ────────────────────────────────────────
   Examples: RabbitMQ, SQS, ActiveMQ                Examples: Kafka, Pulsar, Kinesis, NATS JetStream

   Producer ─► [Queue] ─► ONE consumer              Producer ─► [Append-only Log] ─► Many independent readers
                                                                                      (each keeps own position)
   Message is DELETED after ack                      Message PERSISTS for days/weeks
   Work-to-be-done semantics                         Fact-of-what-happened semantics
   "Send this email"                                 "User 42 logged in at 14:03:22"
   Optimized for routing & dispatch                  Optimized for throughput & replay
   State: AMORTIZED (queue empties)                  State: ACCUMULATED (log grows)
```

**Rule of thumb:** if you'd phrase the work as a **command** ("transcode this video", "send this receipt"), you want a *queue*. If you'd phrase it as a **fact** ("an order was placed", "a payment succeeded"), you want an *event log*.

---

<a name="kafka"></a>
## 1. Apache Kafka — The Distributed Log (Deep Dive)

### What It Is (Analogy First)

Imagine a city's **public ledger book** at the courthouse. Anyone can walk up and **append** an entry ("house #42 sold", "business #7 licensed"). The book is never erased — pages just get older. Any clerk can open the book to any page and **read forward** from there. Two clerks can read at totally different speeds; the book doesn't care.

Kafka is that ledger, digitalized and sharded across a cluster of machines. It is **not a queue** — messages aren't deleted when read. It is an **append-only, replicated, distributed log**.

```
   Producers (writers)                     Consumers (readers, many independent groups)
      │                                              ▲
      │  append                                      │  read from offset
      ▼                                              │
   ┌──────────────────────────────────────────────────────────┐
   │                  THE LOG (Topic)                          │
   │   [0] sale  [1] login  [2] click  [3] sale  [4] logout   │  ← messages NEVER deleted
   └──────────────────────────────────────────────────────────┘
```

### Core Concept: Topic → Partitions → Replicas

A **topic** is the named stream (e.g., `user-events`). A topic is split into **partitions** — ordered, immutable sequences. Partitions are the unit of parallelism AND the unit of replication.

```
   Topic: "orders"  (4 partitions, replication factor = 3)

   Partition 0:  [ord_0][ord_4][ord_8 ][ord_12] ...    ← Leader on Broker 1, Followers on B2,B3
   Partition 1:  [ord_1][ord_5][ord_9 ][ord_13] ...    ← Leader on Broker 2, Followers on B3,B4
   Partition 2:  [ord_2][ord_6][ord_10][ord_14] ...    ← Leader on Broker 3, Followers on B4,B1
   Partition 3:  [ord_3][ord_7][ord_11][ord_15] ...    ← Leader on Broker 4, Followers on B1,B2

   Partition assignment by key:  hash(key) % num_partitions
     e.g. order_id=8472, key="cust-42"  →  hash("cust-42") % 4 = 2  →  Partition 2
```

**Why partitions matter:**
- A single partition gives you **total order** within that key.
- More partitions = more parallel consumers = more throughput.
- The *practical* max is ~7,000 partitions per broker (LinkedIn tested up to ~100k cluster-wide). Past that, ZooKeeper/KRaft overhead bites.

**Broker topology & leader election:**

```
                    ┌─────────────────────────────────────────────┐
                    │           KAFKA CLUSTER (4 brokers)         │
                    │                                             │
   Producer ──────► │  B1: P0(leader)  P2(follower)  P3(follower) │
                    │  B2: P1(leader)  P0(follower)  P3(follower) │
                    │  B3: P2(leader)  P1(follower)  P0(follower) │
                    │  B4: P3(leader)  P2(follower)  P1(follower) │
                    │                                             │
                    │  Controller (one broker, elected) watches   │
                    │  all and reassigns leaders on failure       │
                    └─────────────────────────────────────────────┘
```

Each partition has **one leader** (handles all reads/writes) and **followers** (passively replicate). If the leader dies, the controller promotes an in-sync follower.

### In-Sync Replicas (ISR) — The Heart of Kafka's Durability

```
   Partition 0, replication factor = 3:

   Leader (B1):     [msg0][msg1][msg2][msg3][msg4]   ← receives writes
   Follower (B2):   [msg0][msg1][msg2][msg3][msg4]   ← fully caught up  ✓ in ISR
   Follower (B3):   [msg0][msg1][msg2]______________  ← lagging behind   ✗ removed from ISR

   ISR = {B1, B2}  (only fully-caught-up replicas)
```

When `acks=all`, the producer's write is only confirmed once **all ISR members** have replicated. If too few in-sync replicas remain, Kafka can either:
- **Refuse writes** (`min.insync.replicas` not met) — favors durability.
- **Accept writes anyway** — favors availability (risk of data loss).

This is the single most important trade-off knob in Kafka operations.

### Offsets & Consumer Groups

Each consumer tracks its **offset** — its private bookmark in the log.

```
   Partition 0:   [0]   [1]   [2]   [3]   [4]   [5]   [6]   [7]
                                   ↑                ↑
                            "analytics"        "email-svc"
                            stopped here       reading here

   Two different consumer groups, two different offsets. Neither affects the other.
   Offsets are committed to Kafka's __consumer_offsets topic (not ZooKeeper).
```

**Consumer group rebalancing:**

```
   Topic: "clicks" (3 partitions)     Consumer Group: "etl"

   State A — 3 consumers, even split:
      C1 ◄── P0     C2 ◄── P1     C3 ◄── P2

   State B — C3 crashes → REBALANCE:
      C1 ◄── P0     C2 ◄── P1+P2     (C2 now owns two partitions)
```

**Critical rule:** a consumer group can have at most *N* effective consumers where N = partition count. Extra consumers sit idle. Want more parallelism? Add partitions (but that breaks key→partition mapping for existing keys — a known Kafka headache).

### Key Features

| Feature | Detail |
|---------|--------|
| **Throughput** | Single broker: ~200k msgs/sec on commodity hardware. LinkedIn's cluster: **7 trillion messages/day**. |
| **Retention** | Time-based (default 7 days) or size-based. Messages survive consumption. |
| **Log compaction** | For topics keyed by ID, keeps only the latest value per key — turns Kafka into a slowly-changing table. |
| **Exactly-once (EOS)** | Since 0.11: transactions across topics+consumer offsets. Does NOT cover external sinks (DB writes). |
| **KRaft mode** | Kafka 3.3+ runs without ZooKeeper — metadata stored as its own Kafka topic. |
| **Schema Registry** | Confluent add-on: enforces Avro/Protobuf schema compatibility. |

### Delivery Guarantees

| Producer `acks` | Guarantee | Latency | Use When |
|-----------------|-----------|---------|----------|
| `acks=0` | Fire-and-forget (at-most-once) | Lowest | Telemetry where loss is OK |
| `acks=1` | Leader-only ack (can lose on leader fail) | Low | Most non-critical streams |
| `acks=all` + `min.insync.replicas≥2` | Strong durability (at-least-once) | Higher | Financial/audit events |
| `acks=all` + idempotent producer | No duplicates from retries | Higher | Normal production default |
| `acks=all` + transactions | Exactly-once *between Kafka topics* | Highest | Stream processing pipelines |

**On the consumer side:** Kafka delivers **at-least-once** by default. Exactly-once to a database requires you to make the consumer idempotent (dedupe by message ID, or use the **transactional outbox pattern**).

### Ordering Guarantees

- **Within a partition:** strict FIFO order. Guaranteed.
- **Across partitions:** no ordering guarantee.
- **Stable key → stable partition:** if you key by `user_id`, all events for that user are ordered.
- **Sticky partitioning** (Kafka 2.4+): batches messages into a single partition for throughput, then resets — improves throughput without breaking per-batch order.

### When to Use vs NOT to Use

**✅ Use Kafka when:**
- You need **event sourcing** / CQRS — replay state from the log.
- You need **many independent consumers** of the same stream (analytics, ETL, ML, real-time dashboards).
- Throughput is measured in **hundreds of thousands per second or more**.
- You want a **durable event bus** that survives consumer downtime.
- You're doing **stream processing** (Kafka Streams, Flink, Spark Structured Streaming).

**❌ Do NOT use Kafka when:**
- You need fine-grained **per-message routing** (RabbitMQ is better).
- You need **request/reply** RPC semantics (Kafka is not designed for this).
- Your message volume is **small** (<1k/sec) — operational overhead isn't worth it.
- You need **strict global ordering** across all messages — Kafka only orders within a partition.
- You want messages **deleted immediately after ack** — Kafka retains by design.
- Team is small and you can't afford a dedicated Kafka ops engineer.

### Real Companies Using Kafka

| Company | How They Use Kafka |
|---------|-------------------|
| **LinkedIn** (inventor) | All activity streams, metrics, log aggregation. Origin story. |
| **Netflix** | "Keystone" pipeline: 700B+ events/day. Viewing events, error logs, recommendations input. |
| **Uber** | "Rider-Driver" matching, surge pricing recalculation, trip analytics. All trip events flow through Kafka. |
| **Twitter** | Tweet fan-out to timeline generation; analytics pipeline. |
| **Paytm / PhonePe** (this atlas) | Payment event journal, ledger writes, reconciliation jobs. |
| **Flipkart** | Big Billion Days order events → inventory, pricing, search indexing. |
| **Confluent** | Built an entire company on Kafka + schema registry + ksqlDB. |

### Alternatives & Comparison

Kafka's closest analog is **Apache Pulsar** (same paradigm, separates compute from storage). For simpler needs, **Redis Streams** gives you the log abstraction at smaller scale. **AWS Kinesis** is the managed AWS-only Kafka clone.

---

<a name="rabbitmq"></a>
## 2. RabbitMQ — The Smart Post Office

### What It Is (Analogy First)

Imagine a **post office**. You don't hand your letter directly to a mail carrier — you hand it to a clerk at the counter (the **exchange**). The clerk reads the address and decides which mailbox (**queue**) to drop it into. The mail carrier (**consumer**) picks up mail from their assigned mailbox. Different letters can be routed by zip code, by recipient name, by topic ("all bills go to accounting").

RabbitMQ is that post office: producers hand messages to an **exchange**, the exchange routes them into **queues** based on **bindings**, and consumers pull from queues.

```
                Producer
                   │
                   ▼
            ┌─────────────┐
            │  EXCHANGE   │  ← "where should this go?"
            └──────┬──────┘
                   │  (binding rules)
        ┌──────────┼──────────┐
        ▼          ▼          ▼
    ┌───────┐ ┌───────┐ ┌───────┐
    │Queue A│ │Queue B│ │Queue C│
    └───┬───┘ └───┬───┘ └───┬───┘
        ▼         ▼         ▼
     Worker    Worker     Worker
```

### Core Concept: Exchanges, Queues, Bindings

RabbitMQ's power is the **decoupling** between producer and queue. Four exchange types define the routing logic:

```
   1. DIRECT exchange — exact routing-key match
        exchange ──(routing_key="payments.failed")──► queue "alert-payments"
        exchange ──(routing_key="payments.ok")──────► queue "audit-payments"

   2. TOPIC exchange — wildcard routing-key match
        exchange ──(routing_key="orders.*")────────► queue "all-orders"
        exchange ──(routing_key="orders.electronics")─► queue "electronics-orders"
        * = one word,  # = zero or more words

   3. FANOUT exchange — broadcast to ALL bound queues (ignore key)
        exchange ──► queue "email"  +  queue "sms"  +  queue "analytics"
        (classic pub/sub)

   4. HEADERS exchange — match on message headers, not routing key
        exchange ──(headers: {format: "pdf", region: "eu"})──► queue "eu-pdfs"
```

**Queues** can be:
- **Durable** — survive broker restart (written to disk).
- **Lazy** — always on disk, never RAM (sacrifice speed for huge depth).
- **Quorum** — replicated across nodes using Raft (the modern HA mode; replaces mirrored queues).
- **Priority** — higher-priority messages jump the line.
- **Dead-letter** — messages that fail or expire get forwarded to a DLX queue.

### Key Features

| Feature | Detail |
|---------|--------|
| **AMQP 0.9.1 protocol** | Open standard; clients in every language. Also supports STOMP, MQTT. |
| **Publisher confirms** | Broker ACKs that it accepted & persisted the message. |
| **Consumer acknowledgements** | Consumer must `basic.ack` — unacked messages get redelivered. |
| **Prefetch (QoS)** | Limits unacked messages per consumer — prevents one slow worker hogging the queue. |
| **TTL** | Messages can expire; queues can have max length. |
| **Throughput** | ~20k–50k msgs/sec per node. Clusters of 3–7 nodes typical. |
| **Erlang foundation** | Same VM as WhatsApp — battle-tested for soft-realtime messaging. |

### Delivery Guarantees

- **At-least-once by default** with durable queues + persistent messages + publisher confirms.
- **At-most-once** if you skip acks (auto-ack mode) — fastest, but lossy on crash.
- **Exactly-once is NOT supported**. Use idempotent consumers + dedupe tables.

### Ordering Guarantees

- **Within a single queue, with a single consumer:** strict FIFO.
- **With multiple consumers on one queue:** ordering breaks — messages go to different workers in parallel. (Use a single consumer + internal threading, or sticky routing by key, to preserve order.)
- **Quorum queues** preserve FIFO even across nodes.

### When to Use vs NOT to Use

**✅ Use RabbitMQ when:**
- You need **complex routing** (topic matching, fanout, header-based).
- You're building **task distribution** (competing workers, work queues).
- You need **request/reply** RPC over messaging (RabbitMQ's `reply_to` makes this trivial).
- You want **per-message acknowledgment** and requeue policies.
- Volume is **moderate** (under ~50k/sec sustained).

**❌ Do NOT use RabbitMQ when:**
- You need to **replay history** — messages are deleted on ack.
- You need **millions of msgs/sec** — Kafka/Pulsar are designed for this.
- You need **long-term retention** of events for analytics.
- You want **many independent consumers** to read the same stream at different times.

### Real Companies Using RabbitMQ

| Company | How They Use RabbitMQ |
|---------|----------------------|
| **Reddit** | Internal service-to-service task queues, vote processing. |
| **Tinder** | Match notification fanout, async profile updates. |
| **Adidas** | E-commerce order workflows, payment orchestration. |
| **Discord** (historically) | Used RabbitMQ + eventually built ScyllaDB-based custom system at extreme scale. |
| **Many fintechs** | Payment routing workflows where each step needs explicit ack + retry semantics. |

### Alternatives & Comparison

Closest analog is **Amazon SQS** (managed, simpler routing). **ActiveMQ** is the older Java cousin. **NATS** is the lighter, lower-latency cousin.

---

<a name="sqs"></a>
## 3. Amazon SQS (+ SNS) — The Managed Pair

### What It Is (Analogy First)

Imagine a **self-storage facility**. You drop a box off at the front desk; they put it in a numbered locker. A worker comes by, takes the box out, does the job, and signs a clipboard saying "done." If the worker never signs within a time window, the facility **puts the box back** so someone else can grab it. You never manage the building, the electricity, or the lockers — Amazon does.

That's **SQS** — fully managed, infinite-scale queue. Pair it with **SNS** (the PA system that broadcasts to many queues) and you get a Kafka-lite pub/sub for AWS-native shops.

```
   SQS — work queue (one consumer per message):

      Producer ──► [ SQS Queue ] ──► Worker A pulls msg #1
                                  ──► Worker B pulls msg #2

   SNS + SQS — fanout (each queue gets a copy):

      Producer ──► [ SNS Topic ] ──┬──► SQS Queue "email"    ──► Email Worker
                                   ├──► SQS Queue "sms"      ──► SMS Worker
                                   └──► SQS Queue "audit"    ──► Audit Worker
```

### Core Concept: Visibility Timeout & Dead-Letter Queues

```
   T=0s   Worker pulls msg "resize-img-42"     (msg becomes INVISIBLE to others)
   T=0s..30s   Worker processes...
   T=30s  Worker crashes (no DeleteMessage call)
   T=30s  Visibility timeout expires → msg becomes VISIBLE again
   T=31s  Worker B pulls it, retries

   After N failed deliveries → msg moves to DEAD-LETTER QUEUE for human inspection.
```

**Two queue flavors:**
- **Standard:** unlimited throughput, *at-least-once*, **no ordering guarantee** (messages can arrive out of order or duplicated).
- **FIFO (`*.fifo`):** strict ordering + exactly-once processing, but capped at **300 msgs/sec** (3000 with batching). The throughput tax for order.

### Key Features

| Feature | Detail |
|---------|--------|
| **Fully managed** | No brokers to provision, patch, or scale. |
| **Visibility timeout** | 0s–12h. Prevents double-processing during long jobs. |
| **Long polling** | `WaitTimeSeconds=20` cuts empty responses and your AWS bill. |
| **Message size** | Up to 256 KB; for larger, use S3 + Extended Client Library. |
| **Dead-letter queues** | Configurable `maxReceiveCount` → auto-quarantine poison messages. |
| **Encryption** | SSE-SQS / SSE-KMS at rest, in-transit TLS. |
| **Pricing** | Per-request ($0.40 per million). Free tier: 1M requests/month. |

### Delivery Guarantees

- **Standard SQS:** at-least-once (duplicates possible — design consumers idempotent).
- **FIFO SQS:** exactly-once *delivery* + ordering, but **exactly-once processing still requires you to dedupe by message ID** if your business logic isn't naturally idempotent.
- **SNS:** at-least-once fanout to subscribers.

### Ordering Guarances

- **Standard:** none.
- **FIFO:** strict per message group. Use `MessageGroupId` to shard — within a group, strict FIFO; across groups, parallel.

### When to Use vs NOT to Use

**✅ Use SQS when:**
- You're **already on AWS** and want zero ops overhead.
- You have **bursty, intermittent workloads** — pay only for what you use.
- You need **dead-letter handling** baked in.
- The team has **no Kafka/RabbitMQ operations expertise**.

**❌ Do NOT use SQS when:**
- You need **event replay** — SQS deletes on ack.
- You need **multi-subscriber fanout with independent replay positions** — use SNS+SQS or Kinesis/Kafka.
- You're **multi-cloud** — SQS locks you to AWS.
- You need **>300 msgs/sec with strict ordering** — FIFO cap will bite.

### Real Companies Using SQS

| Company | How They Use SQS |
|---------|-----------------|
| **Amazon itself** | Order processing decoupling, internal service backpressure. |
| **Airbnb** (this atlas) | Booking workflow tasks, image processing queueing. |
| **Netflix** | Buffer between edge services and processing pipelines (alongside Kafka). |
| **Many AWS-native startups** | Default choice for async work — often combined with Lambda triggers. |

### Alternatives & Comparison

Direct competitors: **Kafka** (more powerful, more ops), **RabbitMQ** (more routing features), **Google Pub/Sub** (GCP equivalent).

---

<a name="redis-streams"></a>
## 4. Redis Streams — The In-Memory Conveyor

### What It Is (Analogy First)

Picture a **conveyor belt** in a sushi restaurant. The chef puts plates on the belt; each plate has a number ("#42 salmon roll"). Customers sit along the belt and pick the plates they want, remembering the last number they took. If you walk away and come back later, you can resume from plate #43. The belt moves fast because it's all local — no shipping logistics.

Redis Streams is that conveyor belt: an **in-memory, append-only log** with consumer-group semantics, built natively into Redis.

```
   Producer                    Redis Streams                    Consumers (consumer group)
      │                                                             │
      │  XADD orders * cust 42 amt 99                              │
      ▼                                                             ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Stream "orders"                                                    │
   │  1610000000-0 → {cust:42, amt:99}    ← pending read by worker A     │
   │  1610000001-0 → {cust:43, amt:50}    ← acked by worker B            │
   │  1610000002-0 → {cust:44, amt:12}    ← not yet delivered            │
   └──────────────────────────────────────────────────────────────────────┘
                                   ▲
                       XREADGROUP GROUP etl $ 
```

### Core Concept: IDs, Consumer Groups, PEL

- Each entry has an **auto-generated ID** (timestamp-sequence) like `1610000001234-5`.
- A **consumer group** tracks a shared read position per stream.
- The **Pending Entries List (PEL)** records which messages were delivered but not yet acked — if a consumer crashes, you can **claim** its pending messages with `XPENDING` + `XCLAIM`.
- **`XACK`** removes a message from the PEL.

```
   Consumer Group "etl":
   ┌────────────────────────────────────────────────────────┐
   │ last-delivered-id: 1610000001-0                        │
   │ Workers:                                              │
   │   worker-A: PEL = {1610000000-0 (idle 4s)}            │
   │   worker-B: PEL = {} (all acked)                      │
   └────────────────────────────────────────────────────────┘
```

### Key Features

| Feature | Detail |
|---------|--------|
| **Native to Redis** | No new component to deploy if you already run Redis. |
| **Max length cap** | `XADD ... MAXLEN ~10000` keeps stream bounded (uses approx trimming for speed). |
| **Persistence** | Optional RDB / AOF — survives restart if configured. |
| **Throughput** | ~100k inserts/sec on a single Redis instance. |
| **Consumer groups** | Same concept as Kafka — parallel + independent groups. |
| **TTL via trimming** | Older entries can be evicted automatically. |

### Delivery Guarantees

- **At-least-once** with consumer groups + ack.
- **At-most-once** if you skip acks.
- **No exactly-once** — design consumers idempotent.

### Ordering Guarantees

- **Strict FIFO** within a single stream (entries get monotonically increasing IDs).
- **No partitioning built-in** — for parallelism, you shard by hand: `orders:0`, `orders:1`, `orders:2`.

### When to Use vs NOT to Use

**✅ Use Redis Streams when:**
- You're **already using Redis** and don't want to add Kafka.
- Volume is **moderate** (<100k/sec) and latency-sensitive.
- You need **consumer-group semantics** without Kafka ops overhead.
- Stream lifetime is **short** (hours/days, not weeks).

**❌ Do NOT use Redis Streams when:**
- You need **multi-TB retention** — RAM is expensive; Kafka on disk is cheaper.
- You need **horizontal write scaling** beyond one Redis shard.
- You need **strong durability** — Redis persistence is best-effort; Kafka's ISR is stronger.
- Your stream is the **system of record** — use a real log.

### Real Companies Using Redis Streams

| Company | How They Use Redis Streams |
|---------|---------------------------|
| **Twitter** (historically) | Timeline fanout before moving heavier streams to Kafka. |
| **Instagram** | Lightweight eventing for ephemeral notifications. |
| **Many SaaS apps** | Webhook delivery retries, in-app notification fanout. |
| **Gaming backends** | Real-time leaderboards, match events. |

### Alternatives & Comparison

Think of Redis Streams as **"Kafka for a single Redis box."** If you outgrow it, graduate to Kafka or Pulsar.

---

<a name="nats"></a>
## 5. NATS — The Featherweight Pub/Sub

### What It Is (Analogy First)

Imagine a **taxi dispatch radio**. Drivers tune to a channel and hear broadcasts instantly — no buffering, no storage, just live signal. If a driver is offline, they miss the call. The system is tiny, lightning-fast, and utterly simple. When you need to *record* the calls for replay, you bolt on a tape deck (NATS JetStream).

NATS is that radio: a **lightweight, ultra-low-latency pub/sub system** written in Go. Core NATS is fire-and-forget; **JetStream** adds persistence.

```
   Publisher                                          Subscriber
      │                                                    │
      │  PUB "orders.new" {payload}                        │  SUB "orders.new"
      ▼                                                    ▲
   ┌──────────────────────────────────────────────────────────┐
   │              NATS Server (single Go binary)              │
   │             subject-based routing: "orders.*"            │
   │             "orders.new", "orders.cancel", etc.          │
   └──────────────────────────────────────────────────────────┘
                  ▲                                ▲
                  │                                │
            another sub                       another sub
```

### Core Concept: Subjects, Not Topics

NATS routes on **subjects** — dot-separated hierarchies with wildcards:
- `orders.new`, `orders.cancel`, `payments.completed`
- `orders.*` — matches one token (so `orders.new`, `orders.cancel` but not `orders.new.electronics`)
- `orders.>` — matches one or more tokens (greedy)
- No broker storage — message goes straight from publisher to subscribers in memory.

### Key Features

| Feature | Detail |
|---------|--------|
| **Binary footprint** | ~20 MB. Runs in milliseconds. |
| **Latency** | Sub-millisecond on LAN. |
| **Throughput** | Millions of msgs/sec on a cluster. |
| **JetStream** | Persistence layer: streams, consumers, exactly-once, dedup windows. |
| **Leaf nodes** | Edge NATS servers that bridge to a central cluster — useful for IoT. |
| **Auth** | NATS account + credentials; decentralized multi-tenancy. |
| **Request/reply** | Built-in: `REQUEST "subject" payload` returns the reply. |

### Delivery Guarantees

- **Core NATS:** at-most-once (fire-and-forget). No persistence.
- **JetStream:** at-least-once by default; **exactly-once via dedupe + ack**.

### Ordering Guarantees

- **Core NATS:** in-order delivery per subject per subscriber connection.
- **JetStream:** strict per-stream FIFO; partitioned streams scale parallelism.

### When to Use vs NOT to Use

**✅ Use NATS when:**
- You want **minimal ops** and a single Go binary.
- Latency matters more than durability (microservices internal comms, IoT).
- You're doing **service mesh-style RPC** over messaging.
- Edge/IoT scenarios where leaf nodes bridge to a hub.

**❌ Do NOT use NATS when:**
- You need **heavy event sourcing** with TBs of retention (Kafka is better-tuned for this).
- Your team expects **Kafka-class tooling** (Kafka Connect, ksqlDB, schema registry) — NATS has less.
- You need **mature multi-region replication** — Kafka's MirrorMaker is more battle-tested.

### Real Companies Using NATS

| Company | How They Use NATS |
|---------|-------------------|
| **Tesla** | Vehicle-to-cloud comms (historically; mentioned in NATS case studies). |
| **SAP** | Internal microservices messaging on some products. |
| **Baidu** | Edge node bridging for IoT-style telemetry. |
| **Many fintech microservices** | Internal service bus where Kafka is overkill. |

### Alternatives & Comparison

Closest to **Kafka with JetStream**, but lighter. **Mosquitto/EMQTT** is the IoT-only cousin. For pure RPC, **gRPC** is often a simpler choice.

---

<a name="pulsar"></a>
## 6. Apache Pulsar — The Streaming Cloud

### What It Is (Analogy First)

Imagine Kafka, but the **bookshelf** (storage) and the **librarian** (serving) are **different people in different rooms**. The librarian can be replaced or scaled without touching the shelves. Multiple tenants each get their own labeled shelf section without interfering with each other.

Pulsar is a **streaming platform that separates compute (brokers) from storage (BookKeepers)** — the architectural difference from Kafka, where each broker is both server and storage.

```
                Producers
                   │
                   ▼
   ┌───────────────────────────────┐
   │   Pulsar Brokers (stateless)  │  ← can be killed/restarted freely
   │   route, no disk of their own │
   └───────────────┬───────────────┘
                   │  write to / read from
                   ▼
   ┌───────────────────────────────┐
   │   Apache BookKeeper           │  ← persistent, replicated ledger
   │   (Bookies — storage nodes)   │
   └───────────────────────────────┘
                   ▲
                   │
              Consumers
```

### Core Concept: Tiered Storage + Multi-Tenancy

- **Topics** are organized as `persistent://tenant/namespace/topic`.
- Each topic is a **ledger** stored across multiple **bookies** (replication factor configurable).
- **Tiered storage**: hot data lives on BookKeeper; cold data offloads to S3/GCS automatically.
- **Subscription modes**: Exclusive, Shared, Failover, Key_Shared (key-preserving parallelism — Pulsar's answer to Kafka's per-key ordering).

### Key Features

| Feature | Detail |
|---------|--------|
| **Stateless brokers** | Scale horizontally without data rebalancing — big ops win vs Kafka. |
| **Native multi-tenancy** | Tenant/namespace isolation built-in. |
| **Geo-replication** | Built-in cross-region replication. |
| **Functions** | Compute-on-the-stream: lightweight functions in Java/Python/Go. |
| **Schema Registry** | Built-in, not an add-on. |
| **Throughput** | Comparable to Kafka; Yahoo served 2.8M+ msgs/sec in production. |

### Delivery Guarantees

- **Exclusive/Shared subscriptions:** at-least-once.
- **Key_Shared:** at-least-once with key-ordering across parallel consumers.
- **Exactly-once via transactions** (since 2.6) — like Kafka's EOS but more flexible sink model.

### Ordering Guarantees

- **Per-partition:** strict FIFO.
- **Key_Shared subscription:** preserves per-key order even with multiple consumers — solves a problem Kafka users have to work around manually.

### When to Use vs NOT to Use

**✅ Use Pulsar when:**
- You're a **multi-tenant platform** (SaaS, internal platform team serving many product lines).
- You need **elastic broker scaling** without rebalancing petabytes of data.
- **Geo-replication** is a first-class requirement.
- You want **Kafka semantics** with simpler operations at extreme scale.

**❌ Do NOT use Pulsar when:**
- Your team is small — Pulsar's ops surface (Brokers + BookKeepers + ZooKeeper) is wider than Kafka.
- You want the **Kafka ecosystem** (Kafka Connect, ksqlDB, Debezium) — Pulsar has adapters but less depth.
- Volume is modest — Kafka or Redis Streams is simpler.

### Real Companies Using Pulsar

| Company | How They Use Pulsar |
|---------|---------------------|
| **Yahoo** (inventor) | Internal pub/sub for products, replaced their older Kafka deployment. |
| **Tencent** | Multi-tenant messaging for gaming + social products at billion-user scale. |
| **Splunk** | Data ingestion backbone for their SaaS observability platform. |
| **OneTwoThree (Yahoo Japan)** | Real-time ad serving event streaming. |

### Alternatives & Comparison

Direct head-to-head with **Kafka**. Pulsar wins on multi-tenancy and elastic scaling; Kafka wins on ecosystem maturity and operational familiarity.

---

<a name="celery"></a>
## 7. Celery — The Task Queue for Python

### What It Is (Analogy First)

Imagine a **todo list app on your phone**. You tap "Remind me to call mom" and the app schedules it for later. A worker (you, tomorrow morning) opens the app, sees "call mom," does it, ticks it off. The list is just a list — the actual work happens elsewhere. The list can live in your phone (in-memory) or in the cloud (Redis/Postgres).

Celery is that todo list for Python: a **distributed task queue library** (not a broker itself). It uses Redis/RabbitMQ/SQS as the backend broker and gives your Python code `@app.task` decorators to schedule async work.

```
   Python web request
      │
      │  process_payment.delay(order.id)    ← call .delay(), returns immediately
      ▼
   ┌──────────────────┐
   │  Celery Broker    │  (Redis / RabbitMQ / SQS — Celery doesn't store, just routes)
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │  Celery Worker    │  (separate process: `celery -A tasks worker`)
   │  @app.task        │
   │  def process_pmt()│
   └────────┬─────────┘
            │  writes result
            ▼
   ┌──────────────────┐
   │  Result Backend   │  (Redis / DB — for .get() and status checks)
   └──────────────────┘
```

### Core Concept: Tasks, Brokers, Workers, Beats

- **Task** — a Python function decorated with `@app.task`. Calling it with `.delay()` or `.apply_async()` enqueues it.
- **Broker** — the underlying message transport (Redis, RabbitMQ, SQS, etc.). Celery is broker-agnostic.
- **Worker** — a separate process that polls the broker and executes tasks.
- **Beat** — a scheduler process for periodic tasks (Cron-for-Celery).
- **Result backend** — stores return values so you can check task status: `result.state` → `PENDING/STARTED/SUCCESS/FAILURE`.

### Key Features

| Feature | Detail |
|---------|--------|
| **Python-native** | First-class integration with Django, Flask, FastAPI. |
| **Retries** | `@app.task(autoretry_for=(Exception,), max_retries=3, retry_backoff=True)` |
| **Chords/chains/groups** | Compose workflows: chain tasks, fan out, gather results. |
| **Priority & routing** | Route tasks to different queues by name/priority. |
| **Rate limiting** | Built-in tokens-per-second throttling per task type. |
| **Monitoring** | Flower provides a real-time web dashboard. |

### Delivery Guarantees

- Inherits from the **broker** you pick. With RabbitMQ + `acks_late=True`: at-least-once.
- **Exactly-once is not provided** — design tasks idempotent (dedupe by `task_id`).

### Ordering Guarantees

- **None by default** — tasks are distributed to workers in parallel.
- Within a single worker with `worker_prefetch_multiplier=1`: tasks run one at a time per worker, but order of dequeue is broker-controlled.

### When to Use vs NOT to Use

**✅ Use Celery when:**
- You have a **Python backend** and need background jobs (email sending, report generation, ML inference).
- You want **scheduled tasks** (replacing cron).
- You need **task workflows** (chain, group, chord).

**❌ Do NOT use Celery when:**
- You're not on Python — use Sidekiq (Ruby), BullMQ (Node), or Temporal (polyglot).
- You need **event streaming / log replay** — Celery is a task queue, not an event log.
- You need **exactly-once** — Celery explicitly avoids it.
- Your "tasks" are really **long-running durable workflows** — look at **Temporal** or **AWS Step Functions**.

### Real Companies Using Celery

| Company | How They Use Celery |
|---------|---------------------|
| **Instagram** (early) | Async media processing, fanout tasks. |
| **Mozilla** | Add-on review pipeline, telemetry. |
| **Reddit** | Background work for subreddits and voting. |
| **Many Django shops** | Default async layer for emails, exports, ML jobs. |

### Alternatives & Comparison

- **RQ (Redis Queue):** Simpler Python alternative. Fewer features, less magic.
- **Dramatiq:** Faster, more reliable than Celery; rising in popularity.
- **Temporal:** Durable execution — handles failures and state machines. Heavier, polyglot.

---

<a name="honorable"></a>
## 8. Honorable Mentions

These appear in specific apps in this atlas and are worth knowing:

### MQTT / EMQTT (used by Ola, many IoT)
- **What:** Lightweight pub/sub protocol designed for IoT — tiny header (2 bytes), works over flaky networks.
- **Use case:** Vehicle telemetry (Ola), smart-home devices, industrial sensors.
- **QoS levels:** 0 (fire-and-forget), 1 (at-least-once), 2 (exactly-once handshake).

### AWS Kinesis
- **What:** AWS's managed Kafka-equivalent. Streams → Shards (≈ partitions).
- **Use case:** Log/metric ingestion when you don't want to operate Kafka.
- **Limit:** Per-shard cap (1 MB/s write, 2 MB/s read) — must pre-provision shard count or use on-demand mode.

### Google Cloud Pub/Sub
- **What:** GCP's managed pub/sub. At-least-once delivery, ordering keys for FIFO.
- **Use case:** GCP-native async workflows, BigQuery streaming inserts.

---

<a name="comparison-table"></a>
## 9. 📊 The Master Comparison Table

| Feature | **Kafka** | **RabbitMQ** | **SQS** | **Redis Streams** | **NATS** | **Pulsar** | **Celery** |
|---------|-----------|--------------|---------|-------------------|----------|------------|------------|
| **Family** | Event log | Message queue | Message queue | Event log (single-node) | Pub/sub | Event log | Task queue (library) |
| **Throughput** | Millions/sec | ~50k/sec | Unlimited (managed) | ~100k/sec | Millions/sec | Millions/sec | Broker-limited |
| **Latency** | ~5–50 ms | ~1–20 ms | ~10–100 ms | <1 ms | <1 ms | ~5–50 ms | Seconds (task time) |
| **Persistence** | Disk + replica | Disk + replica | Managed | RAM (+ AOF) | Optional (JetStream) | Disk (BookKeeper) | Broker's choice |
| **Retention** | Days/weeks/forever | Until acked | Until acked (+DLQ) | Until trimmed | Until acked (JetStream) | Tiered (hot+S3) | Until task done |
| **Ordering** | Per-partition | Per-queue (1 consumer) | FIFO queue | Per-stream | Per-subject | Per-partition + Key_Shared | None |
| **Delivery** | At-least-once (+ EOS opt) | At-least-once | At-least-once | At-least-once | At-most-once (+ at-least w/ JS) | At-least-once (+ EOS opt) | At-least-once |
| **Replay history?** | ✅ Yes | ❌ No | ❌ No | ✅ Yes | ❌ (✅ w/ JetStream) | ✅ Yes | ❌ No |
| **Routing** | Key → partition | Rich (4 exchange types) | None | None | Subject wildcards | Key → partition | Queue routing |
| **Multi-subscriber** | ✅ Consumer groups | ✅ Fanout exchange | Via SNS | ✅ Consumer groups | ✅ Natively | ✅ Subscriptions | Per-queue |
| **Ops complexity** | High (ZK/KRaft) | Medium | None (managed) | Low | Low | High (Brokers+BK+ZK) | Low |
| **Managed offering** | MSK, Confluent | CloudAMQP | SQS (native) | ElastiCache | Synadia Cloud | StreamNative, Astra | — |
| **Best for** | Event streaming, analytics | Workflows, task routing | AWS-native async | Light streaming | Microservices, IoT | Multi-tenant platforms | Python background jobs |

---

<a name="decision-tree"></a>
## 10. 🌳 How to Choose — Decision Tree

```
                         START: "I need to send/process messages asynchronously."
                                              │
                                  ┌───────────┴────────────┐
                                  ▼                        ▼
                          Is the work a TASK          Is it an EVENT/FACT?
                          (command: "do X")           ("X happened")
                                  │                        │
                                  ▼                        ▼
                  ┌───────────────┴──────────┐    Need replay / many subscribers / high throughput?
                  │                          │            │
              On AWS +                     Python          ├── YES → Kafka (default) or Pulsar (multi-tenant)
              no ops team?                 backend?        │
                  │                          │            ├── Already on Redis, modest scale? → Redis Streams
                  ▼                          ▼            │
                 SQS                       Celery          ├── IoT / ultra-low latency? → NATS
                                              │            │
                                              └── Need rich routing? → RabbitMQ
```

**Quick rules:**
1. **Default to Kafka** if you're building an event-driven architecture at any real scale.
2. **Default to SQS** if you're AWS-native and want zero ops.
3. **Default to RabbitMQ** if routing complexity is the hard part.
4. **Default to Redis Streams** if you already have Redis and need light streaming.
5. **Default to Celery** if you're a Python shop and need background jobs.
6. **Default to NATS** if latency and footprint matter more than durability.
7. **Default to Pulsar** only if you're a platform team serving many tenants.

---

## 🎯 Key Takeaways

1. **Two families, not seven technologies.** Once you internalize *queue* (RabbitMQ, SQS, Celery) vs *event log* (Kafka, Pulsar, Redis Streams, NATS JetStream), the choice collapses to a few axes.
2. **Exactly-once is almost always a lie.** Even "exactly-once" Kafka transactions only cover Kafka-to-Kafka. For databases and external systems, **idempotent consumers + dedupe tables** are the universal answer.
3. **Ordering costs parallelism.** Every system above makes you trade one for the other — usually via partitions, shards, or message groups.
4. **Retention is the dividing line.** If you need to replay history, you need an event log. Period.
5. **Pick the boring one that fits.** Kafka is overkill for a 100-user SaaS; SQS won't power Netflix's event backbone. Match the tool to your *actual* scale, not your aspirational scale.

---

> **Next:** See how each app in this atlas uses messaging in practice — [Uber's driver-location Kafka stream](../apps/uber.md), [Flipkart's Big Billion Days order pipeline](../apps/flipkart.md), [PhonePe's UPI event journal](../apps/phonepe.md), and [Netflix's Keystone pipeline](../apps/netflix.md).

# Consistency Models — The Complete Guide

> The single most important concept in distributed systems, explained from absolute basics. Every model, every trade-off, every real-world example.

---

## Table of Contents

1. [What Does "Consistency" Even Mean?](#what-is-consistency)
2. [The Two Worlds: ACID vs BASE](#two-worlds)
3. [ACID — Deep Dive](#acid)
4. [BASE — Eventual Consistency](#base)
5. [The CAP Theorem](#cap)
6. [The PACELC Theorem (Better Than CAP)](#pacelc)
7. [Strong Consistency (Linearizability)](#strong)
8. [Eventual Consistency](#eventual)
9. [Causal Consistency](#causal)
10. [Read-Your-Writes Consistency](#read-your-writes)
11. [Session Consistency](#session)
12. [Monotonic Reads](#monotonic-reads)
13. [Monotonic Writes](#monotonic-writes)
14. [Bounded Staleness](#bounded-staleness)
15. [Tunable Consistency (Cassandra/DynamoDB)](#tunable)
16. [Master Comparison Table](#comparison)
17. [How Real Apps Choose](#real-apps)

---

<a id="what-is-consistency"></a>
## What Does "Consistency" Even Mean?

### The Problem (Analogy First)

You and your friend are looking at the same bank account. You deposit ₹10,000 at an ATM in Mumbai. Your friend checks the balance on their phone in Delhi 1 second later.

**Question:** Does your friend see ₹10,000 or the old balance?

```
ATM (Mumbai)              Phone (Delhi)
    │                         │
    │ Deposit ₹10,000         │ Check balance
    ▼                         ▼
┌────────┐  ──replicate──►  ┌────────┐
│ DB Node │   (takes 2s)    │DB Replica│
│  (Mumbai)│                │ (Delhi)  │
└────────┘                  └──────────┘

If friend checks BEFORE replication finishes:
  → Friend sees OLD balance (INCONSISTENT)

If friend checks AFTER replication finishes:
  → Friend sees NEW balance (CONSISTENT)
```

**Consistency** answers: *After a write, how soon are readers guaranteed to see the new value?*

- **Immediately (0 ms delay)?** → Strong consistency
- **Eventually (within a few seconds)?** → Eventual consistency
- **Only for your own writes?** → Read-your-writes consistency

### Why This Is Hard

In a single database on one machine, consistency is free — there's one copy of the data, every read sees the latest write.

But at scale, data is **copied across multiple machines** (replication) and **split across machines** (sharding). Now there are multiple copies, and they might not agree.

```
SINGLE MACHINE (Easy):
  Write ──► [DB] ──► Read always sees latest value. Done.

MULTIPLE MACHINES (Hard):
  Write ──► [DB Node A]
                │
                │ (replicate)
                ▼
            [DB Node B]    ← If user reads from Node B before
                │             replication arrives, they see STALE data
                │
                ▼
            [DB Node C]

The question: What guarantees do we make about when
Node B and Node C will reflect the write?
```

### Consistency ≠ ACID Consistency

**Important:** The word "consistency" is used in two different contexts:

| Context | What It Means |
|---------|--------------|
| **Database ACID** (C in ACID) | Data obeys application rules (no negative balances, foreign keys valid) |
| **Distributed Systems** | Multiple copies of data agree on the latest value |

This guide covers **both** — they're deeply related.

---

<a id="two-worlds"></a>
## The Two Worlds: ACID vs BASE

```
┌─────────────────────┐     ┌─────────────────────┐
│       ACID           │     │       BASE           │
│                      │     │                      │
│ Strong guarantees    │     │ Relaxed guarantees   │
│ Slower at scale      │     │ Faster at scale      │
│ Traditional RDBMS    │     │ NoSQL / distributed  │
│                      │     │                      │
│ "I need correctness" │     │ "I need availability │
│                      │     │  and speed"          │
└─────────────────────┘     └─────────────────────┘

ACID = Atomic, Consistent, Isolated, Durable
BASE = Basically Available, Soft state, Eventual consistency
```

**The fundamental trade-off:**

```
Guarantee Strength
       ▲
       │
  HIGH │  ACID          ← "Every read sees the latest write, always"
       │  (PostgreSQL,   ← But: slower, fewer machines can participate
       │   Spanner)
       │
 MEDIUM │  Causal       ← "Related operations are ordered correctly"
       │  Session       ← "Your own writes are visible to you"
       │
       │
   LOW │  Eventual      ← "Reads might be stale, but will catch up"
       │  (Cassandra,   ← But: fast, always available, infinite scale
       │   DynamoDB)
       ▼
       ──────────────────────────────────────►
              Availability / Scalability
```

---

<a id="acid"></a>
## ACID — The Gold Standard

### Analogy

ACID is like a **bank transaction**. When you transfer ₹1,000 from Account A to Account B:
1. Either the entire transfer happens (debit + credit) or nothing happens
2. The total money in the system never changes
3. Two transfers happening simultaneously don't interfere
4. Once the ATM says "done", it's permanent — even if the power goes out

### The Four Guarantees

#### A — Atomicity (All or Nothing)

```
Transfer ₹1,000 from Account A to Account B:

  Step 1: Debit ₹1,000 from A    ✓
  Step 2: Credit ₹1,000 to B     ✗ (system crashes!)

ATOMICITY guarantees:
  → Step 1 is ROLLED BACK
  → Neither step persists
  → It's as if the transaction never happened

WITHOUT atomicity:
  → Step 1 persists, Step 2 is lost
  → ₹1,000 just vanished. Customer is furious.
```

**How it works:** Databases use a **write-ahead log (WAL)**. Before changing data, the DB writes "I'm about to do X" to the log. If the system crashes, the DB reads the log on recovery and either completes or rolls back each transaction.

#### C — Consistency (Rules Are Enforced)

```
Business rule: "Account balance can never go below ₹0"

  Transfer ₹5,000 from Account A (balance: ₹3,000)

  CONSISTENCY guarantees:
  → Transaction is REJECTED
  → Account A still has ₹3,000
  → The database REFUSES to break the rule

Consistency rules include:
  - Foreign keys (can't delete a user with active orders)
  - Check constraints (age must be >= 0)
  - Unique constraints (no duplicate emails)
  - Triggers (auto-update inventory on order)
  - NOT NULL (every order must have a user_id)
```

#### I — Isolation (Concurrent Transactions Don't Interfere)

```
Two transactions at the same time:

  Transaction 1: Transfer ₹1,000 from A to B
  Transaction 2: Transfer ₹500 from A to C

  Account A starts with ₹2,000

WITHOUT ISOLATION (interleaved execution):
  T1: Read A's balance      → ₹2,000
  T2: Read A's balance      → ₹2,000  ← STALE READ!
  T1: Write A = 2000 - 1000 → ₹1,000
  T2: Write A = 2000 - 500  → ₹1,500  ← OVERWRITES T1! ₹500 lost!

WITH ISOLATION (serialized):
  T1: Read A → 2000. Write A → 1000. Done.
  T2: Read A → 1000. Write A → 500. Done.
  → Correct! No money lost.
```

**Isolation levels** (from weakest to strongest):

```
┌─────────────────────────────────────────────────────────────┐
│  ISOLATION LEVEL          │ What Can Go Wrong               │
├─────────────────────────────────────────────────────────────┤
│                           │                                 │
│  READ UNCOMMITTED         │ Can read uncommitted (dirty)   │
│  (weakest)                │ data from other transactions   │
│                           │                                 │
│  READ COMMITTED           │ No dirty reads, but             │
│  (PostgreSQL default)     │ non-repeatable reads possible  │
│                           │ (read same row twice →          │
│                           │  different values)              │
│                           │                                 │
│  REPEATABLE READ          │ Consistent reads within a      │
│  (MySQL default)          │ transaction, but phantom       │
│                           │ rows possible (new rows        │
│                           │ appear/disappear)              │
│                           │                                 │
│  SERIALIZABLE             │ Full isolation. Transactions   │
│  (strongest)              │ execute as if one-at-a-time.   │
│                           │ No anomalies. Slowest.          │
└─────────────────────────────────────────────────────────────┘
```

**Isolation analogy:** Imagine people in a library.
- **Read Uncommitted:** You can read someone's draft while they're still writing.
- **Read Committed:** You can only read pages that are "published" (committed).
- **Repeatable Read:** Once you read a page, it's photocopied for you — re-reading gives the same content even if someone else changed the original.
- **Serializable:** Only one person in the library at a time.

#### D — Durability (Once Saved, Never Lost)

```
  User clicks "Place Order"
    │
    ▼
  Database writes to:
    1. WAL (write-ahead log) on SSD     ← durable
    2. Data page in buffer pool (RAM)   ← fast but volatile
    3. Periodically flushed to disk     ← durable

  DURABILITY guarantee:
  → Once "Order Placed" is confirmed, the data is on disk
  → Even if the power cord is pulled, the data survives
  → Achieved via fsync() — forces OS to flush to physical disk
```

**Durability levels:**

| Level | What It Means | Risk |
|-------|--------------|------|
| `fsync = off` | OS buffers writes in RAM | Data loss on power failure |
| `synchronous_commit = off` | DB acknowledges before WAL flush | Lose last few transactions on crash |
| `synchronous_commit = on` (default) | DB waits for WAL flush before ack | Safe. Minor latency increase |
| `synchronous_commit = remote_apply` | Wait for replicas to also apply | Safest. Highest latency |

### When ACID Matters

```
Is data correctness non-negotiable?

  Bank transfer?        → YES → ACID (PostgreSQL)
  Payment processing?   → YES → ACID (MySQL + ACID)
  Order placement?      → YES → ACID
  Inventory deduction?  → YES → ACID

Is data approximate/soft?

  View count?           → NO  → Eventual is fine
  Like count?           → NO  → Eventual is fine
  Recommendation feed?  → NO  → Eventual is fine
  "X people viewing" ?  → NO  → Eventual is fine
```

---

<a id="base"></a>
## BASE — Eventual Consistency

### The BASE Philosophy

```
B - Basically Available:    System stays available during failures.
                            You might get stale data, but you get a response.

S - Soft state:             Data can change without a new write
                            (because replicas are syncing in background).

E - Eventual consistency:   If no new writes happen, eventually
                            all replicas will converge to the same value.
```

### Why BASE Exists

ACID requires coordination — all nodes must agree before a transaction commits. At global scale, this is slow:

```
ACID across regions:
  Mumbai write → Ask Virginia → Ask Dublin → All agree? → Commit

  Latency: 100-300ms per write (speed of light between continents)
  Throughput: Limited by slowest node
  Availability: If Virginia is down, transaction fails

BASE across regions:
  Mumbai write → Mumbai node accepts → Respond immediately (5ms)
                → Async replicate to Virginia, Dublin

  Latency: 5ms per write (local only)
  Throughput: Each region operates independently
  Availability: Virginia being down doesn't affect Mumbai
```

**Trade-off:** For 1-5 seconds, Dublin might not see Mumbai's write. But the system never goes down and writes are always fast.

---

<a id="cap"></a>
## The CAP Theorem

### The Famous Triangle

```
                    C (Consistency)
                   ╱ ╲
                  ╱   ╲
                 ╱     ╲
                ╱       ╲
               ╱  PICK   ╲
              ╱    TWO    ╲
             ╱             ╲
            ╱_______________╲
  A (Availability)     P (Partition Tolerance)
```

**CAP says:** During a network partition (two nodes can't communicate), you must choose:
- **CP:** Be consistent (reject requests until partition heals)
- **AP:** Be available (serve potentially stale data)

### What Each Letter Means

```
C — Consistency:
  Every read receives the most recent write or an error.
  If Node A accepted a write, Node B must return that write on next read.

  "I'd rather return an ERROR than stale data."

A — Availability:
  Every request receives a response (not an error).
  The response might not be the latest data, but you always get SOMETHING.

  "I'd rather return STALE DATA than an error."

P — Partition Tolerance:
  The system continues to operate despite network failures
  (communication breaks between nodes).

  "Networks WILL fail. I must handle it."
```

### The CAP Reality

**You can't drop Partition Tolerance.** Networks WILL fail. Cables get cut, routers crash, DNS goes down. So the real choice is:

```
During a network partition, do you:

  CP (Consistency + Partition Tolerance):
  ┌────────┐     ✗(network broken)     ┌────────┐
  │ Node A  │◄────────────────────────►│ Node B  │
  │ (Mumbai)│       CAN'T TALK          │(Virginia)│
  └────────┘                            └────────┘

  Write to A → A can't reach B → A REJECTS the write
  → User sees error: "Service temporarily unavailable"
  → But data stays consistent
  → Examples: Spanner, HBase, MongoDB (with majority reads)

  AP (Availability + Partition Tolerance):
  ┌────────┐     ✗(network broken)     ┌────────┐
  │ Node A  │◄────────────────────────►│ Node B  │
  │ (Mumbai)│       CAN'T TALK          │(Virginia)│
  └────────┘                            └────────┘

  Write to A → A accepts and responds (doesn't need B)
  Write to B → B accepts and responds (doesn't need A)
  → Both writes succeed
  → But A and B now have different data (DIVERGENT)
  → Later: when network heals, they sync (eventual consistency)
  → Examples: Cassandra, DynamoDB, CouchDB
```

### CAP Is Misleading (See PACELC)

CAP only applies **during a partition**. But what about when everything is fine (no partition)? That's what PACELC addresses.

---

<a id="pacelc"></a>
## The PACELC Theorem (Better Than CAP)

### Pronounced "Pack-elk"

```
PACELC breaks into two scenarios:

IF Partition (P):  choose between A (Availability) and C (Consistency)
ELSE (E):          choose between L (Latency) and C (Consistency)
```

```
┌─────────────────────────────────────────────────────────────┐
│                                                              │
│  DURING PARTITION (network failure):                        │
│    PA  — Prefer Availability (serve stale data)             │
│    PC  — Prefer Consistency (reject request)                │
│                                                              │
│  WHEN NO PARTITION (normal operation):                      │
│    EL  — Prefer Low Latency (don't coordinate, allow stale) │
│    EC  — Prefer Consistency (coordinate across nodes)       │
│                                                              │
└─────────────────────────────────────────────────────────────┘

Real systems:

  PA/EL:  Cassandra, DynamoDB     → Always fast, always available, sometimes stale
  PC/EC:  Spanner, HBase          → Always consistent, sometimes slow/unavailable
  PA/EC:  MongoDB (configurable)  → Available during partition, consistent otherwise
```

**Example:**

```
Cassandra (PA/EL):
  Partition: Accept writes on any node (PA). Sync later.
  Normal:    Don't wait for replicas. Respond immediately (EL).
             → Fast, always available. May return stale reads.

Spanner (PC/EC):
  Partition: Reject writes that can't reach majority (PC).
  Normal:    Use Paxos + atomic clocks for global agreement (EC).
             → Slow writes (~100ms). Always consistent.
```

---

<a id="strong"></a>
## Strong Consistency (Linearizability)

### What It Is

**Analogy:** Imagine a single, universal truth. No matter where you are in the world, when you ask "what's the balance?", you get the same answer — the absolute latest value.

```
Strong consistency guarantees:

  1. If Write(X=5) completes at time T,
     ALL reads after time T return X=5.

  2. If Read(X) returns 5, all future reads return ≥5
     (until a new write changes it).

  3. Operations appear to execute in a single, global order.
```

### How It's Achieved

```
Strong consistency requires SYNCHRONOUS coordination:

  Write to Node A
    │
    ├── Node A sends write to Node B ──► Node B acknowledges
    ├── Node A sends write to Node C ──► Node C acknowledges
    │
    ├── Majority acknowledged? (2 of 3)
    │   YES → Write confirmed to client
    │
    └── Read from any node:
        Node must check with majority before responding
        → Ensures latest value is always returned

  This is called "majority quorum" (Paxos or Raft consensus)
```

### The Cost of Strong Consistency

```
WRITE LATENCY:
  Single node:          1ms
  3-node quorum:       10-30ms (2x round trips for agreement)
  Global (3 regions):  100-200ms (speed of light between continents)

AVAILABILITY:
  If majority of nodes are down → system STOPS accepting writes
  (2 of 3 nodes down = system unavailable for writes)
```

### When to Use Strong Consistency

| ✅ Need Strong Consistency | Example |
|---------------------------|---------|
| Money / payments | Can't double-spend ₹1000 |
| Inventory deduction | Can't sell the same item to two people |
| Booking (tickets, seats) | Can't double-book a seat |
| User authentication | Password change must take effect immediately |
| Distributed locks | Two processes must not hold the same lock |

### Real Systems

| System | Mechanism |
|--------|-----------|
| **Google Spanner** | Paxos + atomic clocks (TrueTime) |
| **etcd** | Raft consensus |
| **Zookeeper** | ZAB protocol (Paxos variant) |
| **PostgreSQL (synchronous)** | Synchronous replication to at least one replica |
| **MongoDB (majority)** | Write concern "majority" + read concern "majority" |

---

<a id="eventual"></a>
## Eventual Consistency

### What It Is

**Analogy:** Imagine a news website. A reporter publishes a story. Different people around the world see it at slightly different times — some see it immediately, others 5 seconds later, but eventually everyone sees it.

```
Eventual consistency says:

  "If no new writes happen to X,
   then eventually (could be milliseconds, could be seconds)
   all replicas will return the same value for X."

  KEY WORD: EVENTUALLY.

  Between the write and convergence:
  → Some readers see new value
  → Some readers see old value
  → This window is called the "inconsistency window"
```

### Visual Representation

```
Write X=5 at time T=0:

Time ──────────────────────────────────────────────►
     0ms    50ms    100ms    150ms    200ms
     │       │        │        │        │
Node A: [X=5] [X=5]  [X=5]   [X=5]   [X=5]  ← wrote it
Node B: [X=4] [X=4]  [X=5]   [X=5]   [X=5]  ← saw update at 100ms
Node C: [X=4] [X=4]  [X=4]   [X=5]   [X=5]  ← saw update at 150ms

INCONSISTENCY WINDOW: T=0 to T=150ms
  → During this window, reading from different nodes gives different results.
  → After T=150ms: ALL nodes agree. CONSISTENT.
```

### How It's Achieved

```
ASYNC replication (no waiting):

  Write to Node A
    │
    ▼
  Node A responds "OK" immediately (no waiting for other nodes)
    │
    ├── (background) Replicate to Node B (might take 10ms)
    └── (background) Replicate to Node C (might take 50ms)

  Result: Write latency = 1ms (just the local write)
  Cost: Other nodes might be stale for a few ms
```

### When Eventual Consistency Is Good Enough

| ✅ Eventual Is Fine | Why |
|--------------------|----|
| Social media likes | Off by a few likes for 2 seconds? Nobody cares. |
| View counts | YouTube view count doesn't need real-time accuracy. |
| Product ratings | 4.2 vs 4.3 stars for 5 seconds is irrelevant. |
| News feed ordering | Seeing a slightly old feed is OK. |
| Search index updates | New product takes 5 seconds to appear in search. Fine. |
| Analytics data | Dashboards don't need real-time precision. |

### The Danger Zone: When Eventual Consistency Breaks Things

```
SCENARIO: Selling concert tickets

  User 1: Checks ticket availability → "2 tickets left" (reading from Node A)
  User 2: Checks ticket availability → "2 tickets left" (reading from Node B, stale)

  User 1: Buys 2 tickets → Node A: "0 tickets left"
  User 2: Buys 2 tickets → Node B: "0 tickets left" (but Node B had stale data!)

  RESULT: 4 tickets sold, only 2 available. OVERSOLD.
  FIX: Use strong consistency for inventory. Period.
```

---

<a id="causal"></a>
## Causal Consistency

### What It Is

**Analogy:** If Alice replies to Bob's email, you can't see Alice's reply before seeing Bob's original email. The reply is *caused by* the original — cause must come before effect.

```
Causal consistency guarantees:

  If Operation B was CAUSED BY Operation A
  (e.g., B is a reply to A, or B was written after reading A),
  then every node must see A before B.

  BUT: If Operation C has NO relationship to A or B,
       its ordering relative to A and B doesn't matter.
```

### Example

```
Facebook conversation:

  Alice: "Just got engaged!" (Post A)
  Bob:   "Congratulations!"  (Post B — caused by A, because Bob read A before writing B)
  Carol: "I love pizza!"    (Post C — NOT caused by A or B)

Causal consistency guarantees:
  → Everyone sees A before B (B is a response to A)
  → C can appear anywhere (before A, between A and B, after B — doesn't matter)

  CORRECT ordering:     A → B → C  or  C → A → B  ✓
  INCORRECT ordering:   B → A → C  ✗ (seeing reply before original = confusing!)
```

### How It Works (Lamport Timestamps / Vector Clocks)

```
Each node maintains a "vector clock" — a list of [node: counter] pairs:

  Alice's post:     {[Alice:1]}                        → No dependencies
  Bob's reply:      {[Alice:1], [Bob:1]}              → Depends on Alice's post
  Carol's comment:  {[Alice:1], [Bob:1], [Carol:1]}  → Depends on both

  When a node receives events:
  → Check vector clocks to determine causal order
  → Deliver events only after their causes have been delivered
```

### When to Use Causal Consistency

| ✅ Use Causal | Why |
|--------------|----|
| Comment threads | Replies must follow originals |
| Collaborative docs | Edits must preserve logical order |
| Chat messages | Message order matters within a conversation |

### Real Systems

| System | How |
|--------|-----|
| **Cassandra (LIGHTWEIGHT transactions)** | Paxos for serial consistency (causal) |
| **COPS (MIT research)** | Causally consistent key-value store |
| **MongoDB (causal consistency session)** | Causal consistency for related operations |

---

<a id="read-your-writes"></a>
## Read-Your-Writes Consistency

### What It Is

**Analogy:** If you post a photo on Instagram and immediately check your profile, you expect to see your photo. If it's not there, you'd think the app is broken.

```
Read-your-writes guarantee:

  If YOU write X=5,
  then YOUR next read of X must return 5 (not the old value).

  Other users might not see X=5 yet — that's OK.
  But YOU must see your own write.
```

### Why This Matters (Real User Experience)

```
WITHOUT read-your-writes:

  User: Updates profile name to "Manav"
    │
    ▼
  Write goes to Master DB (Node A)
    │
  User: Immediately views profile
    │
    ▼
  Read goes to Replica (Node B)
  Replica B hasn't synced yet → returns OLD name
    │
    ▼
  User sees: "Rohit" (old name)
  User thinks: "My update didn't work!"
  User updates again... and again...

  BAD USER EXPERIENCE.
```

### How to Achieve Read-Your-Writes

**Method 1: Route reads from same user to master**
```
  User writes → Master
  User reads  → Master (for a short window after write)

  Problem: Master gets overloaded if all reads go there.
```

**Method 2: Sticky sessions**
```
  User writes → Node A
  User reads  → Node A (always route this user to same node)

  Problem: If Node A goes down, user loses their session.
```

**Method 3: Timestamp/Version tracking**
```
  User writes X=5 at timestamp T=1000
    │
    ▼
  Client remembers: "I wrote X at T=1000"
    │
    ▼
  User reads X
    │
    ▼
  Client checks replica's replication timestamp: T=995
  → Replica hasn't reached T=1000 yet → WAIT
  → Retry until replica has T >= 1000 → Read

  Most elegant solution. Used by Amazon DynamoDB.
```

**Method 4: Write-through cache**
```
  User writes X=5
    │
    ├── Write to DB
    └── Write to cache simultaneously

  User reads X → Cache hit → X=5 ✓ (always consistent)
```

### Real Systems

| System | How |
|--------|-----|
| **Amazon DynamoDB** | "Read after write consistency" option |
| **Facebook** | Routes your own reads to master for 5 seconds after write |
| **Instagram** | Your own feed is always read from the freshest source |

---

<a id="session"></a>
## Session Consistency

### What It Is

An extension of read-your-writes. Within a user's **session** (period of active use), consistency guarantees apply. Between sessions, no guarantees.

```
Session consistency guarantees (within one session):
  1. Read-your-writes: You see your own writes
  2. Monotonic reads: You never see data go backwards in time
  3. Monotonic writes: Your writes are applied in the order you sent them

Outside the session:
  → No guarantees (eventual consistency)
```

### Example

```
User's session starts at T=0:

  T=0:   User opens app
  T=5:   User writes: comment = "Great product!"     ← write W1
  T=6:   User reads: comment → must see "Great product!" ← read-your-writes ✓
  T=10:  Another user writes: comment = "Terrible!"   ← someone else's write
  T=11:  User reads: comment
           → May see "Great product!" (if replica hasn't synced) ← OK within session
           → OR may see "Terrible!" (if synced) ← also OK
  T=20:  User closes app → Session ends

  T=25:  User reopens app → New session
  T=26:  User reads: comment → may see either value (new session, no carry-over)

KEY: Within a session, the user's experience is consistent.
     The user never sees the world "go backwards."
```

### How It's Implemented

```
  Client maintains a "session token" (containing last-seen timestamp/version)

  ┌────────┐                        ┌────────────┐
  │ Client  │── session_token ────►│ Load Balancer│
  │         │                       │              │
  │         │◄── routes to same ───│  Routes to   │
  │         │    server for this   │  same replica│
  │         │    session           │  for this    │
  └────────┘                       │  session     │
                                    └────────────┘
```

---

<a id="monotonic-reads"></a>
## Monotonic Reads

### What It Is

**Analogy:** Imagine reading a book where page 50 suddenly becomes page 30 — you went backwards. That would be confusing. Monotonic reads prevent this: once you see data at time T, you never see data from before T.

```
Monotonic reads guarantee:

  If you've read value V1 at time T1,
  your next read (at time T2 > T1) returns V1 or something NEWER.

  You NEVER see older data than what you've already seen.
```

### Why This Matters

```
WITHOUT monotonic reads:

  User reads timeline → sees posts up to 12:00 PM (from Replica A)
  User refreshes     → sees posts up to 11:30 AM (from Replica B, stale!)

  Posts DISAPPEAR! User thinks the app is broken or their posts were deleted.

WITH monotonic reads:
  User reads timeline → sees posts up to 12:00 PM
  User refreshes     → Replica B is at 11:30 AM → WAIT until B catches up → Return 12:00 PM+

  Posts never disappear. Timeline only moves forward.
```

### How to Achieve

```
  Client tracks: "last_read_timestamp = T100"

  On each read:
    → Check replica's replication timestamp: T95
    → T95 < T100? Replica is stale → WAIT or try another replica
    → T95 >= T100? OK, proceed with read
    → Update last_read_timestamp to max(last_read_timestamp, replica_timestamp)
```

---

<a id="monotonic-writes"></a>
## Monotonic Writes

### What It Is

If you send writes W1, W2, W3 in that order, they must be applied in that order on all nodes.

```
WITHOUT monotonic writes:

  User sets profile name: "Manav"    (W1)
  User sets profile name: "Manav K"  (W2)

  If W1 goes to Node A and W2 goes to Node B:
  → Node B applies W2 first, Node A applies W1 first
  → After sync: Node A might end up with "Manav" (W1 overwrote W2!)
  → User sees "Manav" even though they last set "Manav K"

WITH monotonic writes:
  → W1 always applied before W2, on all nodes
  → Final state: "Manav K" (correct)
```

---

<a id="bounded-staleness"></a>
## Bounded Staleness

### What It Is

Data might be stale, but only up to a known limit — either time-based or version-based.

```
Bounded staleness: "Data can be at most T seconds old" or "at most N versions behind"

TIME-BASED:
  "Reads return data that's at most 10 seconds old"
  → Replica lag is monitored. If lag > 10s, route read to master.

VERSION-BASED:
  "Reads return at most 2 versions behind the latest"
  → Track version numbers. If replica is > 2 versions behind, reject or wait.

EXAMPLE:
  Latest write: V=10
  Replica has: V=8

  Bounded staleness (max 2 behind): V=8 is 2 behind V=10 → OK, serve
  Bounded staleness (max 1 behind): V=8 is 2 behind V=10 → TOO STALE, wait
```

### Real Systems

| System | How |
|--------|-----|
| **DynamoDB** | "Consistent read" vs "Eventually consistent read" option per request |
| **Cosmos DB** | 5 consistency levels including "Bounded Staleness" (max K versions or T seconds) |
| **Spanner** | "Exact staleness" reads — read data that's at most 10s old |

---

<a id="tunable"></a>
## Tunable Consistency (Cassandra / DynamoDB Style)

### The Big Idea

Instead of forcing one consistency level for everything, let the developer **choose per query**. A banking operation needs strong consistency; a view counter doesn't.

### Cassandra's Consistency Levels

```
WRITE consistency: How many replicas must acknowledge the write?

  ANY:      Write to at least 1 node (any node, even a hinted handoff)
            → Fastest. Weakest. Data could be lost if that node dies.

  ONE:      Write to at least 1 replica
            → Fast. Data survives if that replica doesn't die before replication.

  QUORUM:   Write to majority of replicas (> N/2)
            → For RF=3, need 2 acknowledgements
            → Strong: If you also read at QUORUM, reads always see latest writes

  LOCAL_QUORUM: Quorum within the local datacenter
            → Good for multi-DC: low latency + decent consistency

  ALL:      Write to ALL replicas
            → Strongest guarantee. Slowest. If any replica is down, write fails.

READ consistency: How many replicas must respond before returning data?

  ONE:      Read from 1 replica → might be stale
  QUORUM:   Read from majority → if combined with QUORUM writes, strong consistency
  ALL:      Read from all replicas → returns latest, but slowest
```

### The Magic Formula: QUORUM Reads + QUORUM Writes = Strong Consistency

```
WHY does QUORUM + QUORUM = strong consistency?

  Replication Factor (RF) = 3 (data is on 3 nodes)
  Quorum = 2 (majority of 3)

  Write at QUORUM: 2 of 3 nodes have the latest write
  Read at QUORUM:  2 of 3 nodes respond

  By pigeonhole principle: at least 1 node must be in BOTH groups
  → The read MUST hit at least one node with the latest write
  → Latest value is always returned!

  ┌──────────────────────────────────────────┐
  │                                          │
  │  WRITE at QUORUM:    Nodes [A, B] have   │
  │                       the latest value    │
  │                                          │
  │  READ at QUORUM:     Reads from [B, C]   │
  │                       (B has it!)        │
  │                                          │
  │  Coordinator compares  │
  │  values from all responding nodes,       │
  │  picks the one with the latest timestamp │
  │                                          │
  │  → Returns latest value. ALWAYS.         │
  │                                          │
  └──────────────────────────────────────────┘

  Mathematical proof:
  - Write hits ≥ ⌈(RF/2)+1⌉ nodes
  - Read hits ≥ ⌈(RF/2)+1⌉ nodes
  - Total nodes hit: ≥ 2×⌈(RF/2)+1⌉ > RF
  - So at least one node is in BOTH sets. QED.
```

### Practical Decision Guide for Cassandra

```
What's the operation?

  → User clicks "Buy" (payment)
    → Write: QUORUM, Read: QUORUM (strong consistency)
    → Prevents double-charging

  → User views product page (read product info)
    → Read: ONE (fast, slight staleness OK)

  → User's own profile update
    → Write: QUORUM
    → Read: LOCAL_QUORUM (balance of speed + consistency)

  → Analytics / metrics write
    → Write: ONE (don't care about consistency for metrics)

  → Critical configuration change
    → Write: ALL (every node must have it)
```

### DynamoDB's Approach

```
DynamoDB offers two read types:

  Eventually consistent read (default, cheaper):
    → Read might return data that's ~1 second stale
    → Consumes 0.5 read capacity units per 4KB
    → Use for: feeds, counts, non-critical data

  Strongly consistent read:
    → Read returns the latest data, always
    → Consumes 1.0 read capacity unit per 4KB (2x cost!)
    → Use for: banking, inventory, critical data

  // DynamoDB Read Example
  {
    "TableName": "Orders",
    "Key": {"order_id": {"S": "ORD-12345"}},
    "ConsistentRead": true   ← strong consistency
  }
```

---

<a id="comparison"></a>
## Master Comparison Table

| Model | Guarantee | Latency | Availability | Complexity | Best For |
|-------|-----------|---------|-------------|------------|----------|
| **Strong (Linearizable)** | Every read sees latest write | High (coordination needed) | Lower (fails if majority down) | High | Banking, payments, inventory |
| **Sequential** | Operations see a consistent order | Medium | Medium | Medium | Multi-player systems |
| **Causal** | Related operations are ordered | Medium | High | Medium | Comment threads, chat |
| **Read-Your-Writes** | You see your own writes | Low-Medium | High | Low | User profile updates |
| **Session** | Consistent within a session | Low | High | Low | Web app sessions |
| **Monotonic Reads** | Never see data go backwards | Low | High | Low | News feeds, timelines |
| **Bounded Staleness** | Data at most T seconds old | Low-Medium | High | Medium | Near-real-time dashboards |
| **Eventual** | All replicas converge eventually | Lowest | Highest | Lowest | Likes, views, feeds |
| **Weak** | No guarantees | Lowest | Highest | Lowest | Logs, best-effort metrics |

---

<a id="real-apps"></a>
## How Real Apps Choose Consistency

### The Universal Pattern

No app uses ONE consistency model. They mix:

```
┌─────────────────────────────────────────────────────────────────┐
│                    FLIPKART'S CONSISTENCY MAP                    │
│                                                                 │
│  STRONG (ACID):                                                  │
│  ├── Payment processing     → PostgreSQL, sync commit           │
│  ├── Inventory deduction    → PostgreSQL row locks              │
│  ├── Order creation         → PostgreSQL transaction            │
│  └── Coupon validation      → PostgreSQL unique constraint       │
│                                                                 │
│  READ-YOUR-WRITES:                                              │
│  ├── Order status           → "Your order is confirmed!"         │
│  └── Cart updates           → User adds item → sees it instantly │
│                                                                 │
│  EVENTUAL:                                                      │
│  ├── Product ratings        → "4.3 stars" (updated periodically) │
│  ├── View counts            → "1,234 people viewed this"        │
│  ├── Recommendations        → "Customers also bought..."         │
│  └── Search index           → New products appear in ~5 seconds  │
│                                                                 │
│  SESSION:                                                       │
│  ├── Browsing history       → User sees their recent views      │
│  └── Cart across tabs       → Multiple tabs see same cart       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    WHATSAPP'S CONSISTENCY MAP                    │
│                                                                 │
│  STRONG:                                                        │
│  ├── Message ordering       → Messages within a chat are ordered│
│  ├── Delivery status        → ✓✓ double-tick must be accurate   │
│  └── Read receipts          → Blue ticks must be correct        │
│                                                                 │
│  EVENTUAL:                                                      │
│  ├── Last seen              → Might be delayed by a few seconds │
│  ├── Contact list sync      → Updated in background             │
│  └── Status/Stories         → Eventual is fine                  │
│                                                                 │
│  CAUSAL:                                                        │
│  └── Reply to message       → Reply must appear after original  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    PHONEPE'S CONSISTENCY MAP                     │
│                                                                 │
│  STRONG (NON-NEGOTIABLE):                                       │
│  ├── UPI transaction         → Exactly-once, no double-charge   │
│  ├── Balance check           → Must show accurate balance        │
│  ├── Settlement to merchant  → T+1, must be exact               │
│  └── Idempotency             → Same request = same result       │
│                                                                 │
│  EVENTUAL:                                                      │
│  ├── Transaction history     → Might take 2-3 seconds to appear │
│  ├── Merchant dashboard      → Updated periodically             │
│  └── Analytics/insights      → Batch processed                  │
└─────────────────────────────────────────────────────────────────┘
```

### Cosmos DB's 5 Levels (The Most Flexible)

Azure Cosmos DB is unique — it offers 5 consistency levels you can pick per query:

```
┌────────────────────────────────────────────────────────────┐
│                                                             │
│  STRONGEST                              Strong              │
│  ←─────────────────────────────────────                     │
│  All reads see latest write                                 │
│                                                             │
│                       Bounded Staleness                     │
│  ←─────────────────────────────────────                     │
│  Reads are at most K versions or T seconds behind           │
│                                                             │
│                   Session                                    │
│  ←─────────────────────────────────────                     │
│  Read-your-writes + monotonic reads within a session        │
│                                                             │
│                 Consistent Prefix                           │
│  ←─────────────────────────────────────                     │
│  See writes in order, but might not see latest yet          │
│                                                             │
│  WEAKEST                                Eventual            │
│  ←─────────────────────────────────────                     │
│  Reads might be stale, but converge eventually              │
│                                                             │
│  Trade-off: Strong → more latency, less availability       │
│             Eventual → lowest latency, max availability     │
└────────────────────────────────────────────────────────────┘
```

---

## Common Interview Questions

**Q: Explain CAP theorem.**
A: During a network partition (communication failure between nodes), you must choose between Consistency (reject requests, preserve correctness) and Availability (serve requests, accept stale data). Partition Tolerance is not optional — networks always fail. So the real choice is CP vs AP. CP systems (Spanner, HBase) reject writes during partitions. AP systems (Cassandra, DynamoDB) accept writes and sync later.

**Q: What's the difference between ACID consistency and distributed consistency?**
A: ACID consistency (the C in ACID) means data obeys application rules — no negative balances, valid foreign keys, no constraint violations. Distributed consistency means multiple replicas of data agree on the latest value. A system can have ACID consistency without distributed consistency (e.g., a single PostgreSQL server) and vice versa.

**Q: How does Cassandra achieve tunable consistency?**
A: Cassandra lets you set consistency level per query. For writes: ANY (acknowledge from 1 node), ONE, QUORUM (majority), ALL. For reads: ONE, QUORUM, ALL. The magic: QUORUM writes + QUORUM reads = strong consistency, because the read and write quorums must overlap by at least one node (pigeonhole principle). That overlap node always has the latest value.

**Q: When would you choose eventual consistency over strong consistency?**
A: When the cost of strong consistency (higher latency, lower availability) outweighs the benefit of immediate correctness. Examples: social media likes (off by 5 likes for 2 seconds = nobody cares), product recommendations (slightly stale suggestions are fine), analytics dashboards (data 5 seconds old is acceptable). Never use eventual consistency for money, inventory, or booking — where staleness causes real-world problems (double-charging, overselling, double-booking).

**Q: What is read-your-writes consistency and why does it matter?**
A: It guarantees that after you write data, your subsequent reads reflect that write. Without it, a user who updates their profile name and immediately views their profile might see the old name, think the update failed, and retry. Achieved by routing the user's reads to the master for a short window after their write, or by tracking timestamps and waiting for replicas to catch up before responding.

**Q: Explain the PACELC theorem.**
A: PACELC extends CAP. During a Partition, choose Availability or Consistency (that's the PAC part). When there's no partition (Else), choose Latency or Consistency (that's the ELC part). So Cassandra is PA/EL — always available, always fast, but may be stale. Spanner is PC/EC — always consistent, but slower and less available. This is better than CAP because it addresses normal operation, not just failures.

**Q: How would you design consistency for a chat app?**
A: Mix models. Message delivery and ordering within a conversation: strong/causal consistency (messages must be ordered, no missing messages). Read receipts and last-seen: eventual consistency (delayed blue tick is OK for a few seconds). User presence (online/offline): eventual (showing "online" for 10 extra seconds is fine). The conversation itself should be causal — replies must appear after the messages they respond to.

**Q: What's the inconsistency window and how do you minimize it?**
A: The inconsistency window is the time between a write and when all replicas have that write. It depends on replication lag — how far behind replicas are from the master. Minimize it by: (1) using faster networks between nodes, (2) increasing replication frequency, (3) using synchronous replication (eliminates window but increases write latency), (4) reading from master for consistency-sensitive queries, (5) using quorum reads.

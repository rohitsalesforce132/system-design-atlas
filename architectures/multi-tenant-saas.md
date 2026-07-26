# Multi-Tenant SaaS Platform — Tenant Isolation, Billing & Control Plane

> How to design a B2B SaaS platform (think Slack, Notion, Linear, Jira) where hundreds or thousands of organizations share one codebase but each tenant's data must stay isolated, billed correctly, rate-limited independently, and — for some customers — physically stored in a specific country. This is the architecture of the **control plane** (managing tenants) and the **data plane** (serving their workloads).

---

## Table of Contents

1. [Problem Statement & Requirements](#1-problem-statement--requirements)
2. [Capacity Estimation](#2-capacity-estimation)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Component Selection](#4-component-selection)
5. [Database Schema](#5-database-schema)
6. [API Design](#6-api-design)
7. [Step-by-Step Request Flow](#7-request-flow)
8. [Scaling Strategy](#8-scaling-strategy)
9. [Failure Modes & Mitigation](#9-failure-modes)
10. [Trade-off Analysis](#10-trade-off-analysis)

---

<a id="1-problem-statement--requirements"></a>
## 1. Problem Statement & Requirements

### The Problem in Plain English

You're building a SaaS product — let's say a project management tool like Linear or a helpdesk like Zendesk. Hundreds of companies sign up. Each company is a **tenant**. Tenant A (a 5-person startup) and Tenant B (a 50,000-person enterprise) run on the same software, but:

- Tenant A must **never** see Tenant B's data (isolation)
- Tenant B pays more and expects better performance (noisy-neighbor protection)
- Tenant C is a German bank and demands their data stay in EU data centers (residency)
- Tenant D is on the free plan and should be rate-limited (metering)
- Tenant E wants SSO + audit logs; Tenant F doesn't care (feature flags)
- All of them expect to sign up in 2 minutes without you SSH-ing into a server (self-serve provisioning)

This is the core problem of **multi-tenancy**: how to share infrastructure efficiently while keeping tenants isolated, billed, and configurable.

### Analogy: The Office Building

```
Think of a SaaS platform as an office building:

  SHARED BUILDING (one codebase, one operations team)
    ├── Tenant A: small team, hot-desks in a shared area
    │              (shared DB with tenant_id column — cheapest)
    ├── Tenant B: medium company, private floor, own lock
    │              (schema-per-tenant — isolated but shared DB server)
    ├── Tenant C: big enterprise, entire building wing, own entrance
    │              (database-per-tenant — full isolation)
    └── Tenant D: paranoid bank, owns a separate building entirely
                   (dedicated cluster — single-tenant deployment)

  Building management = Control Plane (provisions tenants, bills them)
  Each tenant's space   = Data Plane (where their data lives + runs)
  Security guards       = Auth, rate limiting, isolation enforcement
  Utility meters        = Billing/metering (track each tenant's usage)

The building manager doesn't build a new building for every tenant —
they share walls, elevators, plumbing. But each tenant's office has a
lock, and Tenant C's auditors can verify their door is separate.
```

### Functional Requirements

| # | Requirement | Description |
|---|-------------|-------------|
| F1 | Tenant signup | Self-serve: company signs up, picks plan, gets a workspace in <2 min |
| F2 | Tenant provisioning | Automatically create DB/schema/namespace, seed admin user, configure plan |
| F3 | User management | Per-tenant users, roles (admin/member/viewer), SSO (SAML/OIDC) for enterprise |
| F4 | Tenant isolation | No tenant can access another's data — enforced at multiple layers |
| F5 | Feature flags per tenant | Enterprise plan gets SSO + audit logs; Free plan doesn't |
| F6 | Usage metering | Track API calls, storage, seats — feed into billing |
| F7 | Billing | Integrate Stripe; charge per seat / per usage; handle upgrades/downgrades |
| F8 | Rate limiting | Free: 100 req/min; Pro: 10K/min; Enterprise: custom — enforced per tenant |
| F9 | Data residency | EU tenants' data stays in EU region; US in US — configurable per tenant |
| F10 | Tenant admin | Tenant admin can export data, manage users, view usage, cancel |

### Non-Functional Requirements

| # | Requirement | Target |
|---|-------------|--------|
| NF1 | Availability | 99.9% standard; 99.99% for enterprise tier (SLA-driven) |
| NF2 | Provisioning latency | New tenant ready in < 60s (DB created, admin user seeded) |
| NF3 | API latency | p99 < 200ms for data-plane requests |
| NF4 | Isolation strength | Zero cross-tenant data leakage — ever (security-critical) |
| NF5 | Noisy-neighbor protection | One tenant's heavy query can't starve others |
| NF6 | Scalability | Support 10,000+ tenants on shared infrastructure |
| NF7 | Auditability | Every tenant-data access logged for enterprise compliance |
| NF8 | Cost efficiency | Shared infra must be cheaper than dedicated per tenant |

### Out of Scope

- The SaaS application's actual domain logic (whether it's project management, CRM, etc.)
- Marketing site, billing portal UI
- Mobile apps (assume web-first)

---

<a id="2-capacity-estimation"></a>
## 2. Capacity Estimation

### 2.1 Tenant Mix & Sizing

```
Assume a mature SaaS with 10,000 tenants:

  Tier breakdown (typical SaaS distribution):
  ┌──────────────┬───────────┬────────┬──────────────┬──────────────────┐
  │ Tier         │ Tenants   │ Seats  │ Total Users  │ Isolation Model  │
  ├──────────────┼───────────┼────────┼──────────────┼──────────────────┤
  │ Free         │ 7,000     │ 5      │ 35,000       │ Shared (tenant_id)│
  │ Pro          │ 2,500     │ 50     │ 125,000      │ Shared (tenant_id)│
  │ Business     │ 400       │ 500    │ 200,000      │ Schema-per-tenant │
  │ Enterprise   │ 100       │ 5,000  │ 500,000      │ DB-per-tenant     │
  └──────────────┴───────────┴────────┴──────────────┴──────────────────┘
  Total users: ~860,000
```

### 2.2 Request Volume

```
Assumptions:
  - Average user: 50 API calls/day (active working sessions)
  - DAU (daily active users): 40% of total = 344,000
  - Daily API calls: 344,000 × 50 = 17.2M calls/day

Peak hour (workday 10:00-11:00, 20% of daily traffic):
  17.2M × 0.20 = 3.44M calls/hour = ~955 calls/second (peak)

  With enterprise tenants bursting (builds, imports):
  Add 30% headroom → ~1,250 calls/sec design capacity

Read : Write ratio ≈ 10 : 1 (typical SaaS CRUD app)
  Reads:  ~1,140/sec
  Writes: ~110/sec
```

### 2.3 Storage Estimation

```
Per-tenant data (the actual SaaS domain data — projects, tickets, docs):
  Free tenant:     ~50 MB (5 users, light usage)
  Pro tenant:      ~500 MB (50 users, attachments)
  Business tenant: ~5 GB (500 users, history, integrations)
  Enterprise:      ~50 GB (5K users, full audit logs)

Total storage across all tenants:
  7,000 × 50MB    = 350 GB
  2,500 × 500MB   = 1,250 GB
  400   × 5GB     = 2,000 GB
  100   × 50GB    = 5,000 GB
  Total ≈ 8.6 TB of tenant data

Control plane metadata (tenant configs, users, billing): ~50 GB total.

Growth: ~20%/month for active SaaS → design for 2× in 6 months.
```

### 2.4 Control Plane vs Data Plane Load

```
Control plane (tenant management, billing, provisioning):
  - Provisioning: ~10 new tenants/day → trivial load
  - Billing: ~10K tenants × monthly cycle → batch job, not real-time
  - Feature flag checks: cached, ~1 ms per check
  - Total control-plane QPS: < 100/sec (very light)

Data plane (actual tenant workloads):
  - This is where 99.9% of traffic lives
  - 1,250 calls/sec, scaling with tenant growth
  - Each call must: resolve tenant → check rate limit → check feature flags
    → route to correct DB/schema → execute → meter usage
```

### 2.5 Bandwidth

```
Avg response size: ~10 KB (JSON with some embedded data)
Outbound: 1,250 calls/s × 10 KB = 12.5 MB/s = 100 Mbps

File uploads (attachments): ~5% of requests, avg 1 MB
  Upload bandwidth: 1,250 × 0.05 × 1 MB = 62.5 MB/s = 500 Mbps

Total egress: ~600 Mbps peak (one region).
```

### 2.6 Compute (Rough Sizing)

```
API servers (data plane): 1,250 req/s, ~5ms CPU each → ~6 cores busy
  → 20 cores with replicas + headroom → 10 pods (2 cores each)

Control plane: trivial → 3 pods

Per-tenant DBs (enterprise, 100 tenants × dedicated):
  Each DB: small instance (2 vCPU, 4GB) → 100 RDS instances
  OR consolidated onto a few large PG servers with logical replication

Shared DBs (free + pro, 9,500 tenants):
  Cluster of 3 PostgreSQL servers (primary + 2 replicas), 32 vCPU each
  Schema-per-tenant for business (400 schemas per server)

Redis (rate limiting + sessions + feature flags cache):
  Cluster of 3 nodes, 16 GB each
```

---

<a id="3-high-level-architecture"></a>
## 3. High-Level Architecture

### 3.1 The Two-Plane Model

The single most important concept: **separate the control plane from the data plane.**

```
┌──────────────────────────────────────────────────────────────────────┐
│                        CONTROL PLANE                                 │
│         (Manages tenants — runs rarely, low traffic)                 │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ Tenant Mgmt  │  │ Billing      │  │ Feature Flag Service     │   │
│  │ Service      │  │ Service      │  │ (per-tenant config)      │   │
│  │              │  │              │  │                          │   │
│  │ - Provision  │  │ - Stripe sync│  │ - Plan → features map    │   │
│  │ - Deprovision│  │ - Invoices   │  │ - Override per tenant    │   │
│  │ - Plan change│  │ - Dunning    │  │ - Cached in Redis        │   │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘   │
│         │                 │                        │                 │
│         ▼                 ▼                        ▼                 │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │           CONTROL PLANE DATABASE (PostgreSQL)                │    │
│  │  tenants, tenant_configs, subscriptions, users, metering     │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
         │                                              │
         │ provisioning commands                  feature flags + rate limits
         │ (create DB, seed data)                 (read at request time)
         ▼                                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        DATA PLANE                                    │
│         (Serves tenant requests — high traffic, hot path)            │
│                                                                      │
│  ┌──────────┐                                                       │
│  │ Customer │  HTTPS                                                │
│  │ (Tenant X│   │                                                   │
│  │  user)   │   ▼                                                   │
│  └──────────┘ ┌──────────────────────────────────────────────────┐  │
│               │              API GATEWAY                          │  │
│               │  (TLS, auth, resolve tenant from subdomain)       │  │
│               └────────────────────┬─────────────────────────────┘  │
│                                    │                                 │
│               ┌────────────────────▼─────────────────────────────┐  │
│               │         TENANT MIDDLEWARE                         │  │
│               │  1. Resolve tenant_id from request                │  │
│               │  2. Check rate limit (Redis, per-tenant bucket)   │  │
│               │  3. Load feature flags (Redis cache)              │  │
│               │  4. Set DB connection context (tenant routing)    │  │
│               └────────────────────┬─────────────────────────────┘  │
│                                    │                                 │
│               ┌────────────────────▼─────────────────────────────┐  │
│               │         APPLICATION SERVICE                      │  │
│               │  (the actual SaaS business logic)                │  │
│               │  Stateless, horizontally scalable                │  │
│               └────────────────────┬─────────────────────────────┘  │
│                                    │                                 │
│               ┌────────────────────▼─────────────────────────────┐  │
│               │         TENANT-AWARE DATA ROUTER                  │  │
│               │                                                   │  │
│               │  tenant isolation strategy → target DB:           │  │
│               │  ├── shared    → Shared PG (WHERE tenant_id = ?)  │  │
│               │  ├── schema    → Shared PG (SET search_path)      │  │
│               │  └── database  → Dedicated PG instance            │  │
│               └─────┬───────────────┬──────────────┬─────────────┘  │
│                     │               │              │                 │
│         ┌───────────▼──┐  ┌────────▼────────┐  ┌──▼──────────────┐  │
│         │ SHARED DB    │  │ SCHEMA-PER-     │  │ DB-PER-TENANT   │  │
│         │ (PostgreSQL) │  │ TENANT          │  │ (dedicated      │  │
│         │              │  │ (PostgreSQL     │  │  PG instances)  │  │
│         │ Free + Pro   │  │  multi-schema)  │  │ Enterprise      │  │
│         │ tenants      │  │ Business tenant │  │ tenants         │  │
│         └──────────────┘  └─────────────────┘  └─────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Redis Cluster (rate limits, sessions, flag cache)           │   │
│  │  Object Storage (S3 — attachments, per-tenant prefixes)      │   │
│  │  Metering Pipeline (Kafka → usage events → ClickHouse)       │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 The Three Isolation Models (and When to Use Each)

This is the heart of multi-tenant design. One size does NOT fit all tenants.

```
┌──────────────────────────────────────────────────────────────────────┐
│ MODEL 1: SHARED DATABASE, SHARED SCHEMA (tenant_id column)           │
│                                                                      │
│  One DB, one schema, every table has tenant_id. All tenants share.   │
│                                                                      │
│  ┌─────────────────────────────────────────────┐                    │
│  │  projects table                              │                    │
│  │  ┌────┬───────────┬──────┬──────────────┐   │                    │
│  │  │ id │ tenant_id │ name │ created_at   │   │                    │
│  │  ├────┼───────────┼──────┼──────────────┤   │                    │
│  │  │ 1  │ tenant_A  │ Proj │ ...          │   │                    │
│  │  │ 2  │ tenant_A  │ Proj │ ...          │   │                    │
│  │  │ 3  │ tenant_B  │ Proj │ ...          │   │                    │
│  │  │ 4  │ tenant_C  │ Proj │ ...          │   │                    │
│  │  └────┴───────────┴──────┴──────────────┘   │                    │
│  └─────────────────────────────────────────────┘                    │
│                                                                      │
│  Every query: SELECT * FROM projects WHERE tenant_id = ? AND ...     │
│                                                                      │
│  ✓ Cheapest (max sharing, minimal infra)                             │
│  ✓ Easiest to provision (just insert a row)                          │
│  ✗ Noisy neighbor risk (one tenant's big query slows all)            │
│  ✗ Every query MUST filter by tenant_id (a bug = data leak)          │
│  ✗ Hardest to migrate a tenant out later                             │
│                                                                      │
│  USE FOR: Free tier, small tenants, high-density cost optimization   │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ MODEL 2: SHARED DATABASE, SCHEMA-PER-TENANT                          │
│                                                                      │
│  One PostgreSQL instance, but each tenant gets their own schema      │
│  (namespace). Tables are isolated at the schema level.               │
│                                                                      │
│  ┌─────────────────────────────────────────────────────┐            │
│  │  PostgreSQL Instance                                 │            │
│  │  ├── schema_tenant_A/                                │            │
│  │  │     ├── projects                                  │            │
│  │  │     ├── tasks                                     │            │
│  │  │     └── users                                     │            │
│  │  ├── schema_tenant_B/                                │            │
│  │  │     ├── projects                                  │            │
│  │  │     └── tasks                                     │            │
│  │  └── schema_tenant_C/                                │            │
│  │        └── ...                                       │            │
│  └─────────────────────────────────────────────────────┘            │
│                                                                      │
│  Connection sets: SET search_path = 'schema_tenant_A';               │
│  Then: SELECT * FROM projects;  (no tenant_id needed!)               │
│                                                                      │
│  ✓ Better isolation (schema-level, not just row-level)               │
│  ✓ Easier backup/restore per tenant (pg_dump one schema)             │
│  ✓ Can migrate a tenant to its own DB later (logical dump)           │
│  ✗ More schemas = more planning overhead in PG (thousands = slow)    │
│  ✗ Connection pool must route per-tenant (search_path per conn)      │
│                                                                      │
│  USE FOR: Mid-tier tenants, cost-isolation balance                   │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ MODEL 3: DATABASE-PER-TENANT (dedicated instance)                    │
│                                                                      │
│  Each tenant gets their own PostgreSQL database (or instance).       │
│                                                                      │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐         │
│  │ Tenant A DB    │  │ Tenant B DB    │  │ Tenant C DB    │         │
│  │ (own instance) │  │ (own instance) │  │ (own instance) │         │
│  │                │  │                │  │                │         │
│  │ projects       │  │ projects       │  │ projects       │         │
│  │ tasks          │  │ tasks          │  │ tasks          │         │
│  └────────────────┘  └────────────────┘  └────────────────┘         │
│                                                                      │
│  ✓ Strongest isolation (no noisy neighbor, no shared failures)       │
│  ✓ Per-tenant backup, encryption keys, maintenance windows           │
│  ✓ Can meet data residency (deploy instance in specific region)      │
│  ✓ Customer can even hold their own encryption keys                  │
│  ✗ Most expensive (dedicated infra per tenant)                       │
│  ✗ Hardest to operate (1000 instances = 1000 things to patch)        │
│  ✗ Schema migrations must run across all instances                  │
│                                                                      │
│  USE FOR: Enterprise tier, regulated industries, data residency      │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.3 How Tenants Get Routed to the Right Model

```
Customer request: https://acmecorp.yoursaas.com/api/projects

  1. Gateway extracts subdomain: "acmecorp"
  2. Lookup: tenant_id = T_42, isolation = DATABASE, db_host = db-t42.us-east-1
  3. Tenant middleware:
     - Verifies rate limit for T_42
     - Loads feature flags for T_42
     - Sets DB connection to route to db-t42
  4. Application service runs query on T_42's dedicated DB
  5. Response returned, usage metered

The application code is IDENTICAL across all three models. The only
difference is how the DB connection is resolved in middleware. This is
the key abstraction that makes mixed isolation work.
```

---

<a id="4-component-selection"></a>
## 4. Component Selection

### 4.1 Database — PostgreSQL (All Three Models)

**Why PostgreSQL for everything:**

```
PostgreSQL supports all three isolation models natively:
  - Shared schema:    just a tenant_id column
  - Schema-per-tenant: CREATE SCHEMA + SET search_path
  - DB-per-tenant:    CREATE DATABASE + separate connection pools

This matters: you start with shared, and as tenants upgrade, you migrate
them to schema-per-tenant or DB-per-tenant WITHOUT changing your app
code or database engine. Same engine = same expertise, same tooling.

Alternatives considered:
  - MongoDB:  Good for shared (filter by tenantId), weaker for 
              schema-per-tenant (no native schema concept). Migrations 
              across models harder.
  - MySQL:    Similar capabilities, but PostgreSQL's schema namespaces
              and Row-Level Security (RLS) are more mature.
  - CockroachDB:  Multi-region strong consistency, but overkill unless 
              you need global writes. Higher cost, lower maturity.

PostgreSQL Row-Level Security (RLS) is a KILLER FEATURE for multi-tenancy:
  - Define policy: tenants can only see rows WHERE tenant_id = current_setting('app.tenant_id')
  - Even if app code forgets to filter, the DB enforces isolation
  - Defense in depth — belt AND suspenders
```

### 4.2 Connection Pooling — PgBouncer + Connection Router

```
Problem: 
  - DB-per-tenant with 100 tenants = 100 connection strings
  - App pods × connections per pod × tenants = connection explosion

Solution: PgBouncer per DB + a connection router in middleware.

  App pod → Connection Router (resolves tenant → pool) → PgBouncer → PG

For shared/schema models: single PgBouncer in front of the shared cluster.
For DB-per-tenant: PgBouncer sidecar per tenant DB, or a pool router
that maintains a small pool per tenant and closes idle ones.

Why not a generic pool: the router must set search_path (schema model)
or pick the right DSN (DB model) per request — tenant-aware routing.
```

### 4.3 Rate Limiting — Redis + Sliding Window

```
Per-tenant rate limiting is non-negotiable for noisy-neighbor protection.

Algorithm: sliding window log (precise) or sliding window counter (approx, cheaper)

  Key: ratelimit:tenant:{tenant_id}:api
  Implementation: Redis sorted set with timestamps, or INCR with TTL.

  Free tier:      100 req/min
  Pro tier:       10,000 req/min
  Enterprise:     100,000 req/min (or unlimited)

Why Redis: 
  - Sub-millisecond checks at the gateway
  - Atomic operations (Lua script for check-and-increment)
  - Cluster mode for scale

Alternative — gateway-native (Kong/Envoy rate limit plugins):
  Good for basic limits, but per-tenant limits require a plugin that
  reads tenant_id + plan from the request — custom logic. Redis + Lua
  is more flexible and works across gateway and app layers uniformly.
```

### 4.4 Feature Flags — Redis-Cached Config Service

```
Feature gating per tenant (plan-based + overrides):

  Base rules: plan → features
    Free:       [basic_projects, 5_users]
    Pro:        [basic_projects, unlimited_users, integrations, api_access]
    Enterprise: [everything, sso, audit_logs, custom_retention, data_residency]

  Per-tenant overrides (for VIPs, beta features):
    T_42 (enterprise): [everything, beta_ai_features]

Implementation:
  - Control plane stores config in PostgreSQL
  - Pushed to Redis on change (pub/sub) — app reads from Redis (1ms)
  - Fallback: if Redis down, app reads from control plane DB (slower, rare)
  - Client-side: flags bundled into the app bootstrap response

Why not LaunchDarkly/Unleash: those are great for experimentation 
(A/B testing with gradual rollout), but for tenant-plan gating, a simple 
plan→features table + Redis cache is sufficient and cheaper. Use hosted 
flag services when you need percentage rollouts and targeting rules.
```

### 4.5 Billing — Stripe + Internal Metering

```
Stripe handles: payment methods, invoicing, dunning, tax.
We handle: metering (what to bill for) + syncing usage to Stripe.

Metering pipeline:
  API Gateway → emit usage event → Kafka → ClickHouse (aggregate)
  → nightly job: compute per-tenant usage → push to Stripe as usage record

Why separate metering from Stripe:
  - Stripe's usage-based billing is limited (fixed dimensions)
  - We want flexible metering (API calls, storage GB, seats, custom units)
  - ClickHouse gives us real-time usage dashboards for tenants too

Stripe alternatives: Chargebee, Recurly, Billingo. Stripe has the best
developer experience and broadest payment method coverage; the trade-off
is vendor lock-in and less customization for complex pricing.
```

### 4.6 Provisioning — Kubernetes + Terraform

```
Tenant provisioning = creating infrastructure (DBs, schemas, configs):

  Shared tenant:     INSERT INTO tenants (...); done. (milliseconds)
  Schema tenant:     CREATE SCHEMA tenant_T42; run migrations; (seconds)
  DB tenant:         Provision RDS instance; create DB; run migrations; (minutes)

For DB-per-tenant, Terraform automates infra:
  - Control plane calls a job that runs `terraform apply` with tenant vars
  - Creates RDS instance, DNS record, secrets in vault, monitoring alerts
  - Reports completion back to control plane

For schema/shared: a worker service runs SQL migrations programmatically
via Flyway/Liquibase, scoped to the new schema/tenant.

Kubernetes: the control plane and data plane both run as K8s deployments.
Per-tenant dedicated deployments (for the most paranoid enterprises) are
Helm-chart-installed into a dedicated namespace or cluster.
```

### 4.7 API Gateway — Envoy (or Kong)

```
Envoy chosen for:
  - First-class Lua/Wasm filters (custom tenant extraction logic)
  - Excellent observability (per-tenant metrics via stats tags)
  - Native gRPC + HTTP/2 support
  - Dynamic configuration (xDS) — add tenant routes without reloads

Tenant extraction strategies (configurable per deployment):
  - Subdomain:    acmecorp.yoursaas.com → tenant "acmecorp"
  - Header:       X-Tenant-Id: T_42
  - Path prefix:  /api/t/T_42/...
  - JWT claim:    token.tenant_id (after auth)

Most SaaS use subdomain (user-friendly) + JWT claim (secure, post-auth).
```

---

<a id="5-database-schema"></a>
## 5. Database Schema

### 5.1 Control Plane Database (PostgreSQL)

```sql
-- The tenant registry — the source of truth for all tenants
CREATE TABLE tenants (
    tenant_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             VARCHAR(255) NOT NULL,          -- "Acme Corp"
    slug             VARCHAR(64) UNIQUE NOT NULL,     -- "acme" (subdomain)
    plan             VARCHAR(32) NOT NULL,            -- FREE, PRO, BUSINESS, ENTERPRISE
    status           VARCHAR(32) NOT NULL,            -- PROVISIONING, ACTIVE, SUSPENDED, DELETED
    isolation_model  VARCHAR(32) NOT NULL,            -- SHARED, SCHEMA, DATABASE
    region           VARCHAR(32) NOT NULL,            -- us-east-1, eu-west-1 (data residency)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ                      -- soft delete (retain for legal)
);
CREATE INDEX idx_tenants_slug ON tenants(slug);
CREATE INDEX idx_tenants_status ON tenants(status);

-- Where each tenant's data lives (depends on isolation_model)
CREATE TABLE tenant_data_locations (
    tenant_id      UUID NOT NULL REFERENCES tenants(tenant_id),
    db_cluster     VARCHAR(128) NOT NULL,    -- "shared-cluster-1" or "db-t42.us-east-1"
    schema_name    VARCHAR(64),              -- "tenant_T42" (for SCHEMA model)
    database_name  VARCHAR(64),              -- "tenant_T42" (for DATABASE model)
    s3_prefix      VARCHAR(128) NOT NULL,    -- "tenants/T42/" for object storage
    PRIMARY KEY (tenant_id)
);

-- Users belong to tenants (many-to-one; a user can be in multiple tenants)
CREATE TABLE users (
    user_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) NOT NULL,
    name        VARCHAR(255),
    password_hash VARCHAR(255),              -- NULL if SSO-only
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email)
);

CREATE TABLE tenant_memberships (
    tenant_id  UUID NOT NULL REFERENCES tenants(tenant_id),
    user_id    UUID NOT NULL REFERENCES users(user_id),
    role       VARCHAR(32) NOT NULL,          -- ADMIN, MEMBER, VIEWER
    status     VARCHAR(32) NOT NULL,          -- ACTIVE, INVITED, REMOVED
    joined_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX idx_memberships_user ON tenant_memberships(user_id);

-- Feature flags: plan defaults + per-tenant overrides
CREATE TABLE plan_features (
    plan         VARCHAR(32) NOT NULL,
    feature_key  VARCHAR(64) NOT NULL,        -- "sso", "audit_logs", "api_access"
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (plan, feature_key)
);

CREATE TABLE tenant_feature_overrides (
    tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id),
    feature_key  VARCHAR(64) NOT NULL,
    enabled      BOOLEAN NOT NULL,
    reason       VARCHAR(255),                -- "beta", "vip", "custom_contract"
    PRIMARY KEY (tenant_id, feature_key)
);

-- Billing: subscriptions mirror Stripe
CREATE TABLE subscriptions (
    subscription_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(tenant_id),
    stripe_customer_id   VARCHAR(128),
    stripe_subscription_id VARCHAR(128),
    plan             VARCHAR(32) NOT NULL,
    seats            INT,                     -- for per-seat billing
    status           VARCHAR(32) NOT NULL,    -- TRIALING, ACTIVE, PAST_DUE, CANCELLED
    current_period_end TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_subscriptions_tenant ON subscriptions(tenant_id);

-- Metering: raw usage events (also streamed to ClickHouse for analytics)
CREATE TABLE usage_events (
    event_id    BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    metric      VARCHAR(64) NOT NULL,         -- "api_calls", "storage_gb", "seats"
    quantity    DECIMAL(12,2) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_usage_tenant_time ON usage_events(tenant_id, occurred_at);

-- Rate limit config per tenant (overrides plan default)
CREATE TABLE tenant_rate_limits (
    tenant_id      UUID PRIMARY KEY REFERENCES tenants(tenant_id),
    requests_per_minute INT NOT NULL,
    burst_limit    INT NOT NULL
);
```

### 5.2 Tenant Data Schema (Applied Per Tenant — Shared/Schema/DB)

```sql
-- This schema runs for EVERY tenant, in their isolated space.
-- In SHARED model: tenant_id column added, RLS policy enforces isolation.
-- In SCHEMA model: lives in tenant_T42 schema, no tenant_id needed.
-- In DATABASE model: lives in dedicated DB, no tenant_id needed.

-- SHARED model adds this to every table:
--   tenant_id UUID NOT NULL REFERENCES tenants(tenant_id)
--   + index on (tenant_id, ...)

CREATE TABLE projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    created_by  UUID NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
    -- SHARED only: , tenant_id UUID NOT NULL
);
CREATE INDEX idx_projects_created_by ON projects(created_by);

CREATE TABLE tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       VARCHAR(255) NOT NULL,
    status      VARCHAR(32) NOT NULL DEFAULT 'TODO',
    assignee_id UUID,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
    -- SHARED only: , tenant_id UUID NOT NULL
);
CREATE INDEX idx_tasks_project ON tasks(project_id, status);
CREATE INDEX idx_tasks_assignee ON tasks(assignee_id, status);

-- Row-Level Security (SHARED model) — defense in depth
-- Even if application bug omits tenant_id filter, DB blocks cross-tenant reads.
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_projects ON projects
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- Application sets the context per request:
--   SET app.tenant_id = 'T_42';
-- Now all queries on projects automatically scope to T_42.
```

### 5.3 Read Models (for Tenant Dashboards)

```sql
-- Materialized view per tenant for "my usage this month"
-- Backed by ClickHouse for scale, exposed via API.

-- ClickHouse: tenant_usage_daily
CREATE TABLE tenant_usage_daily (
    tenant_id   UUID,
    date        Date,
    api_calls   UInt64,
    storage_gb  Decimal(10,2),
    seats       UInt32,
    PRIMARY KEY (tenant_id, date)
);
```

---

<a id="6-api-design"></a>
## 6. API Design

### 6.1 Control Plane API (Tenant Management)

```
# Tenant signup (self-serve)
POST /api/v1/tenants
  Body: { name, slug, plan, ownerEmail, region? }
  → 202: { tenantId, status: "PROVISIONING", estimatedReadyIn: 60 }
  (Async: provisioning runs in background, webhook notifies on completion)

GET  /api/v1/tenants/{tenantId}
  → 200: { id, name, slug, plan, status, isolationModel, region, createdAt }

# Plan management
PUT  /api/v1/tenants/{tenantId}/plan
  Body: { plan: "BUSINESS" }
  → Triggers: possibly migrate isolation (SHARED → SCHEMA),
    update feature flags, sync to Stripe.

# Feature flag management (admin only)
GET  /api/v1/tenants/{tenantId}/features
  → 200: { features: [{ key, enabled, source: "plan"|"override" }] }

PUT  /api/v1/tenants/{tenantId}/features/{key}
  Body: { enabled: true, reason: "vip" }

# User management within tenant
POST /api/v1/tenants/{tenantId}/memberships
  Body: { email, role: "MEMBER" }
  → Sends invite; on accept, creates tenant_memberships row.

# Billing
GET  /api/v1/tenants/{tenantId}/usage?month=2026-07
  → 200: { apiCalls: 1250000, storageGb: 12.5, seats: 42, cost: 420.00 }

POST /api/v1/tenants/{tenantId}/billing/portal
  → 302 redirect to Stripe customer portal (manage cards, invoices)

# Provisioning webhook (internal)
POST /internal/provisioning/{tenantId}/completed
  Body: { status: "ACTIVE", dbCluster: "...", errors?: [...] }
```

### 6.2 Data Plane API (Tenant Application — Per-Tenant)

```
# All requests scoped to a tenant via subdomain or header
Host: acme.yoursaas.com
Authorization: Bearer <JWT with tenant_id claim>

GET  /api/v1/projects
  → 200: { projects: [{ id, name, taskCount }] }

POST /api/v1/projects
  Body: { name, description }
  → 201: { id }

GET  /api/v1/projects/{id}/tasks?status=TODO
  → 200: { tasks: [...] }

# Rate limit headers in every response:
X-RateLimit-Limit: 10000
X-RateLimit-Remaining: 9842
X-RateLimit-Reset: 1721900000
```

### 6.3 Tenant-Aware Authentication

```json
// JWT structure for a user in multiple tenants
{
  "sub": "user_U1",
  "email": "alice@acme.com",
  "tenants": [
    { "id": "T_42", "role": "ADMIN", "slug": "acme" },
    { "id": "T_99", "role": "MEMBER", "slug": "sideproject" }
  ],
  "current_tenant": "T_42",   // set on login / tenant switch
  "exp": 1721900000
}

// Tenant switch:
POST /api/v1/auth/switch-tenant
  Body: { tenantId: "T_99" }
  → Issues new JWT with current_tenant: "T_99"
```

### 6.4 Admin API (for SaaS Operators — Not Tenants)

```
# Operator endpoints (SaaS company staff, not tenant users)
# Separate auth realm (stronger: hardware key, IP allowlist)

GET  /admin/tenants?status=SUSPENDED&plan=ENTERPRISE
POST /admin/tenants/{id}/suspend
  Body: { reason: "nonpayment" }
  → Sets status, revokes data-plane access, triggers dunning.

POST /admin/tenants/{id}/migrate-isolation
  Body: { to: "DATABASE", region: "eu-west-1" }
  → Schedules off-hours migration: dump → provision new → verify → cutover.

GET  /admin/metrics/isolation
  → { shared: 9500, schema: 400, database: 100, totalStorage: "8.6TB" }
```

---

<a id="7-request-flow"></a>
## 7. Step-by-Step Request Flow

### Flow 1: New Tenant Signup (Control Plane)

```
STEP 1: Customer signs up
─────────────────────────
  Browser → POST /api/v1/tenants { name: "Acme", slug: "acme", plan: "PRO" }
  Control Plane API:
    a) Validate slug is unique (idx_tenants_slug)
    b) INSERT INTO tenants (status=PROVISIONING, isolation=SHARED, region=us-east-1)
    c) Publish "tenant.provisioning.requested" event to Kafka
    d) Return 202 { tenantId, status: PROVISIONING }

STEP 2: Provisioning worker picks up the event
───────────────────────────────────────────────
  Provisioning Service consumes the event:
    a) Read tenant config: plan=PRO → isolation_model=SHARED
    b) SHARED model: just need a tenant_id, no new infra
    c) INSERT INTO tenant_data_locations (db_cluster=shared-1, no schema/db)
    d) Seed admin user: INSERT INTO users, tenant_memberships (role=ADMIN)
    e) Set up feature flags: copy plan_features for PRO plan
    f) Set up rate limit: tenant_rate_limits (10000 rpm for PRO)
    g) Create Stripe customer + subscription (trial period)
    h) Publish "tenant.provisioning.completed"

STEP 3: Welcome flow
────────────────────
  Control Plane consumes "completed":
    - Send welcome email with login link
    - Tenant status → ACTIVE
  Customer can now log in at https://acme.yoursaas.com

Total time: ~5-10 seconds for SHARED model (no infra to provision).
For DATABASE model (enterprise): Terraform provisions RDS (3-10 min),
but signup is async — customer gets email when ready.
```

### Flow 2: Tenant User Makes an API Call (Data Plane)

```
Request: GET https://acme.yoursaas.com/api/v1/projects
         Authorization: Bearer <JWT>

STEP 1: Gateway resolves tenant
───────────────────────────────
  Envoy extracts subdomain "acme" → lookup tenants table → tenant_id=T_42
  Verifies JWT signature, checks current_tenant matches T_42
  Forwards to Application Service with X-Tenant-Id: T_42 header

STEP 2: Tenant middleware enforces policies
───────────────────────────────────────────
  a) Rate limit check:
     Redis: INCR ratelimit:T_42:202607261230 (window = current minute)
     If count > 10000 → return 429 with Retry-After header
  b) Feature flag load:
     Redis: GET flags:T_42 → { sso: false, audit_logs: false, api_access: true }
     Attach to request context
  c) Tenant status check:
     If SUSPENDED → return 402 Payment Required (or 403)
  d) Resolve DB connection:
     Lookup tenant_data_locations → db_cluster=shared-1, model=SHARED
     Acquire connection from shared-1 pool
     Execute: SET app.tenant_id = 'T_42';  (enables RLS)

STEP 3: Application logic executes
──────────────────────────────────
  Controller: GET /projects
  Service: SELECT * FROM projects;  
    (RLS auto-filters to tenant_id = T_42 — no explicit WHERE needed)
  Returns: [{ id, name }, ...]

STEP 4: Response + metering
───────────────────────────
  Response returned to gateway → customer
  Gateway emits usage event: { tenant_id: T_42, metric: "api_calls", qty: 1 }
    → Kafka → ClickHouse (aggregated nightly for billing)
  Rate limit counter already incremented in step 2a

STEP 5: Audit (enterprise tenants only)
───────────────────────────────────────
  If feature flag audit_logs=true:
    Log: { tenant_id, user_id, action: "project.list", at, ip }
    → separate audit log store (per-tenant, longer retention)
```

### Flow 3: Tenant Upgrades Plan (SHARED → DATABASE)

```
Enterprise upgrade is the most complex flow — it involves data migration.

STEP 1: Customer upgrades
─────────────────────────
  PUT /api/v1/tenants/T_42/plan { plan: "ENTERPRISE" }
  Control Plane validates payment, sets plan=ENTERPRISE
  isolation_model target = DATABASE, region = eu-west-1 (customer choice)

STEP 2: Schedule migration (off-hours)
───────────────────────────────────────
  Migration is risky → schedule for tenant's off-hours (e.g., 02:00 their TZ)
  Control Plane creates migration job with: source=shared-1, target=new DB

STEP 3: Provision target
────────────────────────
  Terraform: provision RDS in eu-west-1 (meets data residency!)
  Run schema migrations on new DB (CREATE TABLE projects, ...)

STEP 4: Migrate data
────────────────────
  Worker: pg_dump shared-1 with WHERE tenant_id = T_42
    → transform: strip tenant_id column (DB model doesn't need it)
    → pg_restore into new DB
  Verify row counts match

STEP 5: Cutover
───────────────
  a) Set tenant status = MIGRATING (briefly rejects writes, allows reads)
  b) Final delta sync: any rows changed since dump (using updated_at)
  c) UPDATE tenant_data_locations: db_cluster = db-t42-eu, isolation = DATABASE
  d) Connection pools reconfigure (pods pick up new config within seconds)
  e) Set tenant status = ACTIVE

STEP 6: Verify + cleanup
────────────────────────
  Customer confirms everything works
  After 7-day grace period: delete tenant data from shared-1
  (Defense: verify no references to T_42 remain in shared cluster)

Downtime: < 5 minutes during cutover. Done off-hours, customer barely notices.
```

---

<a id="8-scaling-strategy"></a>
## 8. Scaling Strategy

### 8.1 Where the Bottlenecks Are

```
┌──────────────────────┬────────────────────────┬──────────────────────────────┐
│ Component            │ Limiting factor        │ Mitigation                   │
├──────────────────────┼────────────────────────┼──────────────────────────────┤
│ Shared DB            │ Noisy neighbor (I/O)   │ Move heavy tenants to schema/DB│
│ Connection pools     │ Total connections      │ PgBouncer multiplexing       │
│ Redis (rate limits)  │ Hot keys               │ Shard by tenant_id hash      │
│ Gateway              │ TLS CPU                │ Terminate at LB, scale pods  │
│ Schema-per-tenant DB │ Schema count (PG ~fewK)│ Shard tenants across clusters│
│ Provisioning speed   │ DB-per-tenant latency  │ Pool of pre-provisioned DBs  │
└──────────────────────┴────────────────────────┴──────────────────────────────┘
```

### 8.2 Scaling the Shared Database

```
As shared cluster grows (9,500 tenants), one PG primary saturates.

Strategy: shard the shared cluster by tenant_id hash.

  shared-cluster-0: tenants with hash(tenant_id) % 4 == 0  (~2,375 tenants)
  shared-cluster-1: tenants with hash(tenant_id) % 4 == 1
  shared-cluster-2: ...
  shared-cluster-3: ...

  tenant_data_locations.db_cluster = "shared-cluster-2" (per tenant)

When a cluster fills up: split it (add cluster-4, migrate half the tenants).
Migration uses the same dump/restore/cutover flow as plan upgrades.

Read replicas: each shared cluster has 2 read replicas for read scaling
(tenant dashboards, reports — anything that can tolerate slight staleness).
```

### 8.3 Managing Many DB-Per-Tenant Instances

```
100 enterprise tenants = 100 PG instances. Operating challenge:

  Problem 1: Patching / upgrades
    → Automation: runbook that applies upgrades in rolling batches.
      Group tenants by maintenance window (their off-hours).
      Use blue-green: provision new version, replicate, cutover.

  Problem 2: Schema migrations across 100 DBs
    → Migration service: applies migration N to all tenant DBs in parallel
      (with batching — 10 at a time — to avoid blasting all if migration breaks)
    → Track migration version per tenant DB; alert on drift.

  Problem 3: Cost (idle instances)
    → Right-size: most enterprise tenants have light load most of the time.
      Use smaller instances (t3.medium) + serverless (Aurora Serverless)
      that scales to zero when idle. Pay for actual use, not provisioned.

  Problem 4: Connection management
    → Service mesh or connection broker maintains pools to all tenant DBs,
      with idle eviction. Pod doesn't hold 100 connections open — only
      active tenants' connections stay warm, rest reconnect on demand.
```

### 8.4 Scaling Rate Limiting

```
At 1,250 req/s across 10,000 tenants, Redis handles it — but hot keys matter.

  Naive: single key ratelimit:T_42 → all T_42's requests hit one Redis shard.

  Better: 
    - Redis Cluster shards by key (tenant_id is already the natural shard key)
    - Each tenant's rate limit state lives on one shard
    - A tenant's traffic naturally lands on the same shard → no cross-talk

  Lua script for atomic check-and-increment (avoids race conditions):
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], 60) end
    return current
    → returns count; if > limit, reject. One round trip, atomic.
```

### 8.5 Scaling Provisioning Throughput

```
If 100 tenants sign up in an hour (viral launch), provisioning must keep up.

  Shared model: trivial (just DB inserts) → thousands/hour easily
  Schema model: CREATE SCHEMA + migrations → ~10s each, parallelize
  DB model: Terraform RDS provisioning → 5-10 min each

  Trick for DB model: pre-provision a POOL of warm databases.
    - Maintain 5 idle DBs always ready
    - New tenant → assign from pool, run migrations (seconds, not minutes)
    - Background worker provisions replacement into pool

  This trades a little cost (5 idle DBs) for instant enterprise onboarding.
  Critical for sales: "you're provisioned in 30 seconds" closes deals.
```

---

<a id="9-failure-modes"></a>
## 9. Failure Modes & Mitigation

### 9.1 Cross-Tenant Data Leak (The Worst Failure)

```
Scenario: A bug in shared-model query omits tenant_id filter.
  SELECT * FROM projects;  -- forgot WHERE tenant_id = ?
  → Tenant A sees Tenant B's projects. Catastrophic: trust destroyed, legal liability.

MITIGATION — defense in depth (multiple layers):

  Layer 1: ORM-level tenant scoping
    - Force all queries through a repository that injects tenant_id
    - Ban raw SQL without review

  Layer 2: PostgreSQL Row-Level Security (RLS)
    - DB enforces tenant_id = current_setting('app.tenant_id') even if app forgets
    - Test: try to query without setting context → must return 0 rows

  Layer 3: Automated testing
    - Integration tests: create data for T_A and T_B, query as T_A, assert no T_B data
    - Run on every PR

  Layer 4: Runtime canaries
    - Synthetic tenant "Canary_T" with known data
    - Continuously query as real tenants → must never see Canary_T data
    - Alert immediately if leak detected

  Layer 5: Audit logging
    - Every query logged with tenant context
    - Anomaly detection: tenant accessing unexpected row counts

If a leak occurs: incident response, customer notification (legally required 
in many jurisdictions under GDPR/CCPA), post-mortem, and the affected 
isolation model gets a hard review.
```

### 9.2 Noisy Neighbor (Tenant Monopolizes Resources)

```
Scenario: Tenant X runs a massive report query on the shared DB,
          starving all other tenants (slow responses, timeouts).

MITIGATION:

  Layer 1: Per-tenant rate limiting (API layer)
    - Tenant X limited to 10K req/min → can't flood the API

  Layer 2: Query timeouts + statement timeouts
    - PostgreSQL statement_timeout = 30s per connection
    - Long queries killed before they monopolize

  Layer 3: Resource queues (pg_timeout, connection quotas)
    - Limit connections per tenant in shared pool
    - Tenant X can't open 100 parallel connections

  Layer 4: Automatic escalation
    - Monitor per-tenant query latency + resource use
    - If Tenant X consistently heavy → auto-suggest migration to 
      schema-per-tenant or DB-per-tenant (and charge accordingly)

  Layer 5: Read isolation
    - Heavy reads (reports, exports) routed to read replicas
    - Don't compete with the primary's write path
```

### 9.3 Control Plane Outage

```
Scenario: Control plane DB goes down.

Impact:
  - No new signups (can't create tenants) — revenue impact but not critical
  - No plan changes, no billing updates
  - Feature flag changes don't propagate (stale flags served from cache)
  - Data plane KEEPS RUNNING (cached config, existing connections)

MITIGATION:
  - Control plane DB: HA (primary + sync replica + async replicas)
  - Feature flags + rate limits CACHED in Redis at data plane (survives 
    control plane outage for hours — degraded, not dead)
  - Tenant status checks cached with short TTL (10s)
  - Provisioning jobs queued in Kafka (resume when control plane recovers)

Key principle: the DATA PLANE must not depend on the CONTROL PLANE for 
every request. Cache aggressively, tolerate staleness, keep serving.
```

### 9.4 Tenant Data Loss (DB Failure)

```
Scenario: A tenant's dedicated DB (DB-per-tenant model) loses a disk.

MITIGATION:
  - PITR (point-in-time recovery): continuous WAL backup to S3, RPO < 5 min
  - Daily snapshots: full backup, RTO = restore time (~30 min for 50 GB)
  - Cross-region replication for enterprise tier (DR)

For shared model:
  - Same PITR + snapshots, but restore is trickier (can't restore one 
    tenant without affecting others)
  - Solution: logical export per tenant nightly (pg_dump WHERE tenant_id)
    → enables single-tenant restore without touching others

If a tenant asks "restore my data to yesterday":
  - DB-per-tenant: snapshot restore, ~30 min
  - Shared: logical replay from nightly dump, ~1-2 hours
```

### 9.5 Stripe Webhook Failure / Billing Drift

```
Scenario: Stripe webhook for "subscription_cancelled" is missed (network drop).

Impact: Tenant cancelled in Stripe but still ACTIVE in our system → 
        uses the product for free until noticed.

MITIGATION:
  - Webhook idempotency: Stripe retries webhooks; we dedupe by event ID
  - Reconciliation job: nightly, sync subscription status from Stripe API
    → catches any missed webhooks, updates our DB
  - Grace period: if payment fails, suspend after 14 days (not immediately)
    → avoids locking out paying customers due to transient card issues
  - Dunning: automated emails reminding customer to update payment method
```

### 9.6 Migration Failure (Mid-Cutover)

```
Scenario: Tenant upgrade SHARED → DATABASE fails halfway (target DB provisioned,
          data partially migrated, cutover not done).

MITIGATION:
  - Source (shared) is NOT modified during migration — only copied
  - Migration is idempotent: re-running picks up where it left off
  - Cutover is the only "committed" step — before that, abort is safe
  - If cutover fails: tenant stays on shared, migration retried next window
  - Track migration state machine: PROVISIONED → DATA_COPIED → VERIFIED → CUTOVER
    → each step is independently retryable/cleanable
```

### 9.7 Region Failure (Data Residency Implications)

```
Scenario: EU region goes down. EU tenants' data must NOT failover to US 
          (would violate GDPR residency commitments).

MITIGATION:
  - For residency-bound tenants: replication ONLY within the same region
    - EU tenant DB replicates to EU standby only, never to US
    - Accept lower availability in exchange for compliance
  - Document this trade-off in the SLA: residency tenants have 99.9% 
    (regional), non-residency tenants can failover globally for 99.99%
  - Pre-approved regions per tenant: config explicitly lists allowed regions
  - Automation CANNOT place data outside approved regions (guardrail)
```

---

<a id="10-trade-off-analysis"></a>
## 10. Trade-off Analysis

### 10.1 Isolation Model: Cost vs. Isolation

```
┌──────────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│                  │ SHARED       │ SCHEMA       │ DATABASE     │ DEDICATED    │
│                  │ (tenant_id)  │ per-tenant   │ per-tenant   │ (own cluster)│
├──────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Isolation        │ Weak (row)   │ Medium       │ Strong       │ Maximal      │
│ Cost/tenant      │ Lowest       │ Low          │ High         │ Highest      │
│ Density          │ 1000s/DB     │ 100s/DB      │ 1/instance   │ 1/cluster    │
│ Noisy neighbor   │ High risk    │ Medium       │ None         │ None         │
│ Backup/restore   │ Hard (per-row│ Medium       │ Easy         │ Easy         │
│ Migrations       │ 1x           │ 1x per schema│ 1x per DB    │ 1x per tenant│
│ Data residency   │ Hard         │ Medium       │ Easy         │ Easy         │
│ Provisioning     │ Instant      │ Seconds      │ Minutes      │ Hours        │
│ Best for         │ Free/Pro     │ Business     │ Enterprise   │ Regulated    │
└──────────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

CHOICE: Mixed model — assign based on plan. This maximizes revenue per 
unit of cost: cheap tenants share aggressively, expensive tenants get 
isolation they pay for. The cost of supporting multiple models is the 
abstraction layer (tenant-aware connection routing) — worth it.
```

### 10.2 Control Plane vs Data Plane Coupling

```
Choice: Strong separation — data plane survives control plane outage.

Cost:
  - Eventual consistency for config changes (flags, rate limits) — 
    takes seconds to propagate via Redis cache
  - More infrastructure (two planes, caching layer, event bus)
  - Operational complexity (two systems to monitor)

Benefit:
  - Data plane availability is independent of control plane
  - Control plane can be deployed/changed without risking tenant uptime
  - Clearer blast radius for incidents

Alternative — coupled (data plane reads control plane DB per request):
  - Simpler (one DB, one lookup)
  - But every request depends on control plane → single point of failure
  - Unacceptable at scale: one bad control-plane deploy takes down all tenants
```

### 10.3 Synchronous vs. Asynchronous Provisioning

```
Choice: Async provisioning (return 202, notify on completion).

Cost:
  - Customer doesn't get instant access (especially for DB model, minutes)
  - Must handle "provisioning failed" gracefully (retry, notify, refund)
  - More complex status tracking

Benefit:
  - API responds instantly (good UX for the signup form)
  - Provisioning failures don't block the signup flow
  - Can queue and batch provisioning jobs

Mitigation for DB-model latency: pre-provisioned pool (section 8.5) 
collapses minutes to seconds. For SHARED/SCHEMA, provisioning is fast 
enough (~10s) that sync could work — but consistency favors async everywhere.
```

### 10.4 Self-Serve vs. Sales-Assisted Onboarding

```
Choice: Self-serve for Free/Pro/Business; sales-assisted for Enterprise.

Cost:
  - Two onboarding flows to maintain
  - Enterprise customers expect white-glove (manual DB provisioning, 
    custom config, data residency setup)

Benefit:
  - Self-serve scales (1000s of signups with zero sales effort)
  - Sales focuses on high-value accounts where personal touch closes deals

The architecture supports both: self-serve uses the public API; 
sales-assisted uses the admin API with manual steps (custom regions, 
dedicated clusters, SSO setup calls).
```

### 10.5 Stripe vs. Custom Billing

```
Choice: Stripe for payments + internal metering for usage.

Cost:
  - Two systems to reconcile (our metering vs. Stripe's invoice)
  - Vendor lock-in (migrating off Stripe is painful)
  - Limited customization for exotic pricing (per-feature, hybrid)

Benefit:
  - Don't reinvent PCI compliance, tax calculation, dunning
  - Stripe handles global payment methods (cards, UPI, SEPA, etc.)
  - Customer portal (manage cards, download invoices) is built-in

When to go custom: if pricing is extremely custom (usage + per-seat + 
overage tiers + discounts), Stripe's pricing engine fights you. Then 
build metering → custom invoice generator → Stripe just charges the total.
```

### 10.6 Per-Tenant Rate Limiting Algorithm

```
Choice: Sliding window counter (Redis).

Algorithms compared:
  Fixed window:    simple, but bursts at window edges (100 req at 0:59 + 100 at 1:01 = 200 in 2s)
  Sliding log:     precise, but memory-heavy (store every request timestamp)
  Sliding counter: approx (weight current + previous window) — good balance
  Token bucket:    allows bursts up to bucket size — good for "bursty but average-limited"

Cost of sliding counter: slight imprecision at window boundaries (acceptable 
for rate limiting, which is approximate by nature).

Benefit: O(1) memory per tenant (two counters), fast Redis operations.

For enterprise tenants needing guaranteed no-burst: token bucket with 
small burst size. Configurable per tenant via tenant_rate_limits table.
```

### 10.7 Summary — Key Decisions at a Glance

```
┌──────────────────────────┬───────────────────────────────┬───────────────────────────────┐
│ Decision                 │ We Chose                      │ The Price We Pay              │
├──────────────────────────┼───────────────────────────────┼───────────────────────────────┤
│ Isolation                │ Mixed (shared/schema/DB)      │ Abstraction layer complexity  │
│ Database                 │ PostgreSQL (all models)       │ Single-engine dependency      │
│ Architecture             │ Control + Data plane split    │ Two systems to operate        │
│ Provisioning             │ Async + pre-warmed pool       │ Status tracking overhead      │
│ Rate limiting            │ Redis sliding window          │ Approximation at edges        │
│ Feature flags            │ Plan-based + Redis cache      │ Eventual consistency (~10s)   │
│ Billing                  │ Stripe + internal metering    │ Reconciliation + lock-in      │
│ Tenant routing           │ Subdomain + JWT claim         │ DNS + auth coupling           │
│ Data residency           │ Region-pinned, no cross-region│ Lower availability for EU     │
│ Security                 │ RLS + ORM scoping + tests     │ Defense-in-depth upkeep       │
└──────────────────────────┴───────────────────────────────┴───────────────────────────────┘

Every choice trades simplicity for scale, isolation, or compliance.
The RIGHT mix depends on your tenant profile, pricing model, and 
regulatory constraints. Start simple (shared-only), add models as 
enterprise sales demand them.
```

---

## Appendix: The Multi-Tenancy Decision Tree

```
Building a SaaS? Choose your starting isolation model:

  Q1: Are all your customers small/startups (free/pro tier)?
      YES → Start SHARED-only. Simplest, cheapest. Add models when needed.
      NO  → Continue.

  Q2: Do you have enterprise customers paying >$10K/year?
      YES → They'll demand DB-per-tenant eventually. Plan for mixed model.
      NO  → SHARED or SCHEMA is fine.

  Q3: Do customers require data residency (GDPR, HIPAA, government)?
      YES → DB-per-tenant (or dedicated cluster) for those tenants.
            Region-pinned, no cross-region replication.
      NO  → Continue.

  Q4: Is noisy-neighbor protection a hard requirement (SLA-driven)?
      YES → At minimum schema-per-tenant (separate query planning).
            DB-per-tenant for the most sensitive.
      NO  → SHARED with strict rate limiting may suffice.

  Q5: Are you a solo founder / small team?
      YES → SHARED-only. Ship fast. Migrate when revenue justifies it.
            The mixed-model abstraction is overkill until you have 
            enterprise deals in the pipeline.

DEFAULT RECOMMENDATION:
  Day 1: SHARED-only with RLS + rate limiting.
  First enterprise deal: add SCHEMA-per-tenant for them.
  Regulated/customer: add DB-per-tenant with data residency.
  This is the path most successful SaaS companies actually took.
```

---

## Appendix: Key Numbers to Remember

```
Scale targets:
  - 10,000 tenants (7K free, 2.5K pro, 400 business, 100 enterprise)
  - ~860,000 users, ~344,000 DAU
  - 1,250 API calls/sec peak (data plane)
  - <100 calls/sec (control plane — it's light)

Storage:
  - ~8.6 TB tenant data total
  - ~50 GB control plane metadata
  - 20% monthly growth → design for 2x in 6 months

Provisioning latency:
  - SHARED: < 10 seconds (just DB inserts)
  - SCHEMA: 10-30 seconds (create schema + migrate)
  - DATABASE: 5-10 minutes (Terraform) OR < 1 min (pre-warmed pool)

Isolation strength:
  - SHARED: relies on tenant_id + RLS (defense in depth)
  - SCHEMA: schema-level separation (stronger)
  - DATABASE: instance-level separation (strongest, no shared failure domain)

Rate limits (per tenant, per minute):
  - Free: 100
  - Pro: 10,000
  - Enterprise: 100,000 (or custom)

SLA tiers:
  - Standard: 99.9% (~43 min downtime/month)
  - Enterprise: 99.99% (~4 min downtime/month) — requires HA + DR
```

---

*Multi-tenancy is fundamentally about trade-offs between cost efficiency and isolation. There is no "best" model — only the best model for each tenant, given what they pay and what they require. The architectures that survive are the ones that can evolve: start shared, add isolation as revenue demands it, and never, ever leak data across tenants.*

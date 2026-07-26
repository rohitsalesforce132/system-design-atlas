# Architecture: Multi-Channel Notification System

> How to design a notification system that sends email, SMS, push, and in-app notifications to millions of users — with templating, preferences, delivery tracking, retries, and rate limiting.

---

## Table of Contents

1. [Problem Statement & Requirements](#requirements)
2. [Capacity Estimation](#capacity)
3. [High-Level Architecture](#architecture)
4. [Component Selection](#components)
5. [Database Schema](#schema)
6. [API Design](#api)
7. [Request Flow](#flow)
8. [Scaling Strategy](#scaling)
9. [Failure Modes & Mitigation](#failures)
10. [Trade-off Analysis](#tradeoffs)

---

<a id="requirements"></a>
## 1. Problem Statement & Requirements

### Functional Requirements

```
- Send notifications via 4 channels: Email, SMS, Push (mobile), In-App
- Support templated notifications (welcome email, order confirmation, OTP)
- Users can set channel preferences (email yes, SMS no, push yes)
- Track delivery status: sent, delivered, failed, bounced
- Retry failed deliveries (exponential backoff)
- Rate limit per user (don't send > 10 notifications/hour to one user)
- Support priority (transactional OTP > marketing email)
- Support scheduled notifications (send digest at 8 AM)
- Analytics: open rate, click rate, bounce rate per campaign
```

### Non-Functional Requirements

```
- Latency: Transactional (OTP) < 5 seconds end-to-end
- Latency: Promotional < 5 minutes
- Throughput: 10,000 notifications/sec peak
- Availability: 99.9% (notifications are not payment-critical)
- Durability: Never lose a notification record (audit trail)
- Multi-region: Send from nearest region (SMS gateway locality)
```

---

<a id="capacity"></a>
## 2. Capacity Estimation

```
ASSUMPTIONS:
  - 10 million users
  - Average 3 notifications/user/day = 30M notifications/day
  - Peak: 10,000 notifications/sec (sale/event)

  Breakdown by channel:
  - Email:     20M/day (67%) — cheap, most users allow it
  - Push:       7M/day (23%) — mobile users
  - SMS:        2M/day (7%)  — expensive, OTP only
  - In-App:     1M/day (3%)  — when app is open

  Daily: ~30M notifications
  Monthly: ~900M notifications
  Yearly: ~11B notifications

STORAGE:
  - Notification record: ~500 bytes (id, user, channel, template, status, timestamps)
  - 30M/day × 500 bytes = 15 GB/day
  - 1 year: 15GB × 365 = 5.5 TB (store in PostgreSQL, archive to S3 after 90 days)

  - Templates: ~500 templates × 5KB each = 2.5 MB (negligible)

BANDWIDTH:
  - Email body: ~50KB (HTML template with images)
  - SMS body: ~200 bytes
  - Push payload: ~2KB
  - Daily outbound: 20M×50KB + 7M×2KB + 2M×200B = ~1 TB/day

  = ~12 MB/sec sustained, ~50 MB/sec peak

THROUGHPUT:
  - 10,000 notifications/sec peak
  - Each requires: template render + provider API call + DB write
  - Need ~50 worker instances handling 200 notifications/sec each
```

---

<a id="architecture"></a>
## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        NOTIFICATION SYSTEM                           │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                          │
│  │ Order     │  │ User     │  │ Marketing │                          │
│  │ Service   │  │ Service  │  │ Service   │                          │
│  └─────┬─────┘  └─────┬────┘  └─────┬────┘                          │
│        │               │             │                                │
│        ▼               ▼             ▼                                │
│  ┌─────────────────────────────────────────┐                        │
│  │         NOTIFICATION API                 │                        │
│  │  POST /notifications/send                │                        │
│  │  POST /notifications/schedule            │                        │
│  │  GET  /notifications/{id}/status         │                        │
│  └──────────────────┬──────────────────────┘                        │
│                     │                                                │
│  ┌──────────────────▼──────────────────────┐                        │
│  │         KAFKA TOPIC: notifications       │                        │
│  │  (partitions by user_id for ordering)    │                        │
│  └──────────────────┬──────────────────────┘                        │
│                     │                                                │
│        ┌────────────┼────────────┐                                   │
│        ▼            ▼            ▼                                   │
│  ┌──────────┐┌──────────┐┌──────────┐                              │
│  │ Worker    ││ Worker    ││ Worker    │  (auto-scaled)             │
│  │ Pool 1    ││ Pool 2    ││ Pool N    │  50+ workers               │
│  │           ││           ││           │                             │
│  │ For each  ││ For each  ││ For each  │                             │
│  │ message:  ││ message:  ││ message:  │                             │
│  │           ││           ││           │                             │
│  │ 1.Check   ││           ││           │                             │
│  │   prefs   ││           ││           │                             │
│  │ 2.Render  ││           ││           │                             │
│  │   template││           ││           │                             │
│  │ 3.Rate    ││           ││           │                             │
│  │   limit   ││           ││           │                             │
│  │ 4.Send    ││           ││           │                             │
│  │   to chan ││           ││           │                             │
│  └───┬──────┘└───┬──────┘└───┬──────┘                              │
│      │           │           │                                      │
│      ▼           ▼           ▼                                      │
│  ┌──────────────────────────────────────┐                           │
│  │       CHANNEL PROVIDERS               │                           │
│  │                                       │                           │
│  │  ┌─────────┐ ┌────────┐ ┌─────────┐ │                           │
│  │  │ SES      │ │ Twilio │ │ APNS/   │ │                           │
│  │  │ (Email)  │ │ (SMS)  │ │ FCM     │ │                           │
│  │  │          │ │        │ │ (Push)  │ │                           │
│  │  └─────────┘ └────────┘ └─────────┘ │                           │
│  └──────────────────────────────────────┘                           │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ PostgreSQL    │  │ Redis        │  │ S3           │              │
│  │ (notification │  │ (rate limit, │  │ (email body  │              │
│  │  records,     │  │  preferences,│  │  templates,  │              │
│  │  templates)   │  │  dedup)      │  │  archives)   │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

---

<a id="components"></a>
## 4. Component Selection

| Component | Choice | Why | Alternatives Considered |
|-----------|--------|-----|------------------------|
| **Message Queue** | Kafka | High throughput (10K/sec), partitioning by user_id for ordering, event retention for replay | RabbitMQ (simpler but lower throughput), SQS (AWS-only, no ordering by user) |
| **Cache/Rate Limit** | Redis | Atomic INCR for rate limiting, fast preference lookups, dedup sets | Memcached (no data structures for sliding window) |
| **Primary DB** | PostgreSQL | ACID for delivery records, JSONB for template variables, strong query capabilities | MySQL (less JSON support), DynamoDB (no complex queries) |
| **Email Provider** | AWS SES | $0.10 per 1,000 emails (cheapest), high deliverability, scales automatically | SendGrid (more features, more expensive), Mailgun |
| **SMS Provider** | Twilio | Global coverage, reliable delivery, delivery receipts | MSG91 (India-cheap), Plivo |
| **Push Provider** | FCM + APNS | Free, native to mobile platforms | OneSignal (adds another layer) |
| **File Storage** | S3 | Email HTML templates, attachment storage, archival | Local disk (no durability) |
| **Worker Runtime** | Python/Go | Python for SES/Twilio SDKs, Go for high throughput | Node.js (also good) |

---

<a id="schema"></a>
## 5. Database Schema

```sql
-- Notification templates
CREATE TABLE notification_templates (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    channel VARCHAR(20) NOT NULL,         -- 'email', 'sms', 'push', 'in_app'
    subject TEXT,                         -- email subject / push title
    body TEXT NOT NULL,                   -- template with {{variables}}
    template_type VARCHAR(20),            -- 'transactional', 'promotional'
    variables JSONB,                      -- expected variable definitions
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Notification records (audit trail)
CREATE TABLE notifications (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    template_id UUID REFERENCES notification_templates(id),
    channel VARCHAR(20) NOT NULL,
    status VARCHAR(20) DEFAULT 'queued',  -- queued, sent, delivered, failed, bounced
    priority INT DEFAULT 5,               -- 1=urgent(OTP), 5=normal, 10=marketing
    variables JSONB,                      -- actual variable values for this notification
    provider_message_id VARCHAR(200),     -- ID from SES/Twilio/FCM
    scheduled_at TIMESTAMPTZ,             -- for scheduled notifications
    sent_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    failure_reason TEXT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- User notification preferences
CREATE TABLE user_notification_preferences (
    user_id BIGINT NOT NULL,
    channel VARCHAR(20) NOT NULL,         -- 'email', 'sms', 'push', 'in_app'
    enabled BOOLEAN DEFAULT true,
    category_preferences JSONB,           -- {"order_updates": true, "marketing": false}
    PRIMARY KEY (user_id, channel)
);

-- Delivery events (for analytics)
CREATE TABLE notification_events (
    id BIGSERIAL PRIMARY KEY,
    notification_id UUID NOT NULL REFERENCES notifications(id),
    event_type VARCHAR(20) NOT NULL,      -- 'sent', 'delivered', 'opened', 'clicked', 'bounced'
    event_timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB                        -- bounce reason, click URL, etc.
);

-- Indexes
CREATE INDEX idx_notifications_user ON notifications(user_id, created_at DESC);
CREATE INDEX idx_notifications_status ON notifications(status) WHERE status IN ('queued', 'failed');
CREATE INDEX idx_notifications_scheduled ON notifications(scheduled_at) WHERE scheduled_at IS NOT NULL;
```

---

<a id="api"></a>
## 6. API Design

```yaml
# Send a notification immediately
POST /notifications/send
{
    "user_id": 12345,
    "template_name": "order_confirmed",
    "channel": "email",
    "priority": 3,
    "variables": {
        "order_id": "ORD-12345",
        "total": 2999,
        "items": ["Nike Shoes", "Socks"]
    }
}
Response: 202 Accepted
{
    "notification_id": "uuid-here",
    "status": "queued"
}

# Schedule a notification
POST /notifications/schedule
{
    "user_id": 12345,
    "template_name": "weekly_digest",
    "channel": "email",
    "scheduled_at": "2024-07-28T08:00:00Z",
    "variables": { ... }
}

# Check delivery status
GET /notifications/{notification_id}/status
Response:
{
    "notification_id": "uuid-here",
    "status": "delivered",
    "sent_at": "2024-07-26T10:00:00Z",
    "delivered_at": "2024-07-26T10:00:02Z"
}

# Update user preferences
PUT /users/{user_id}/preferences
{
    "email": { "enabled": true, "marketing": false },
    "sms": { "enabled": true, "marketing": false },
    "push": { "enabled": true, "marketing": true }
}

# Webhook from provider (SES/Twilio delivery receipt)
POST /webhooks/delivery
{
    "notification_id": "uuid-here",
    "event": "delivered",
    "timestamp": "2024-07-26T10:00:02Z"
}
```

---

<a id="flow"></a>
## 7. Request Flow — Sending an Order Confirmation

```
Step 1: Order Service triggers notification
  Order Service ──POST /notifications/send──► Notification API
  { user_id: 12345, template: "order_confirmed", channel: "email",
    variables: { order_id: "ORD-12345", total: 2999 } }

Step 2: API validates and enqueues
  Notification API:
    1. Validate template exists
    2. Create notification record (status=queued) in PostgreSQL
    3. Push to Kafka topic "notifications" (key=user_id for ordering)
    4. Return 202 Accepted to Order Service

Step 3: Worker picks up notification
  Worker (consuming from Kafka):
    1. Read notification from Kafka
    2. Fetch user preferences from Redis
       → user 12345: email enabled, marketing disabled
       → This is transactional → allowed
    3. Check rate limit:
       Redis: GET rate:12345:email:2024072610
       → Current count: 3, limit: 10/hour → OK
       → INCR rate:12345:email:2024072610
    4. Render template:
       Load "order_confirmed" template from PostgreSQL
       Replace {{order_id}} → "ORD-12345", {{total}} → "2999"
       → Rendered HTML email

Step 4: Send to channel provider
  Worker ──AWS SES API──► Amazon SES
  → SES sends email to user
  → SES returns message_id

Step 5: Update record
  Worker updates PostgreSQL:
    status = 'sent'
    provider_message_id = SES message ID
    sent_at = NOW()

Step 6: Delivery receipt (async)
  Amazon SES ──webhook──► Notification API
  { event: "delivered", message_id: "..." }
  → API updates notification: status='delivered', delivered_at=NOW()

Step 7: If failed — retry
  If SES returns error or no delivery in 5 min:
    → status = 'failed', retry_count++
    → If retry_count < 3: push back to Kafka with delay
    → Exponential backoff: 30s, 2min, 10min
    → If retry_count = 3: status='permanently_failed', alert ops
```

---

<a id="scaling"></a>
## 8. Scaling Strategy

```
BOTTLENECK 1: Worker throughput
  - 10,000 notifications/sec peak
  - Each worker: ~200 notifications/sec (limited by provider API latency)
  - Need: 50 workers minimum
  - Solution: Auto-scale worker pool based on Kafka consumer lag
  - Kafka partitions ≥ worker count (for parallelism)

BOTTLENECK 2: Provider rate limits
  - SES: 14 emails/sec per account initially (can request increase)
  - Twilio: 10 SMS/sec per number (use multiple numbers)
  - FCM: 600,000/sec (generous)
  - Solution: Connection pooling, batch API calls (SES bulk email)

BOTTLENECK 3: Database writes
  - 10,000 inserts/sec (notification records)
  - Solution: Kafka absorbs the spike, workers write at steady rate
  - Partition PostgreSQL by created_at (monthly partitions)
  - Archive records > 90 days to S3

BOTTLENECK 4: Template rendering
  - Jinja2 rendering: ~1ms per template
  - Solution: Cache rendered templates in Redis (for same variables)
  - Pre-compile templates at startup
```

---

<a id="failures"></a>
## 9. Failure Modes & Mitigation

| Failure | Impact | Mitigation |
|---------|--------|------------|
| **Kafka down** | No new notifications processed | Run Kafka cluster (3+ brokers). If fully down, API writes to PostgreSQL directly (fallback queue) |
| **Redis down** | Rate limiting fails, preferences unavailable | Fallback: allow notification (better to over-send than miss OTP). Use Redis Sentinel for HA |
| **SES/Twilio down** | Email/SMS not delivered | Retry with exponential backoff. Fallback to alternate provider (SendGrid/MSG91). Alert ops |
| **Worker crash** | Notification stuck in queue | Kafka retains messages. Another worker picks up. Idempotent processing (check status before sending) |
| **Database down** | No status tracking | Accept notification to Kafka (fire-and-forget for OTPs). Replicate to standby. Replay from Kafka when DB recovers |
| **Rate limit Redis corruption** | Users get spammed | Hard limit at provider level (SES max sends per 24h). Circuit breaker on notification API |

---

<a id="tradeoffs"></a>
## 10. Trade-off Analysis

| Decision | Choice | Trade-off |
|----------|--------|-----------|
| **Sync vs Async sending** | Async (Kafka) | Lower latency for caller (202 in 10ms), but user doesn't know if delivery succeeded immediately |
| **Template rendering** | Server-side | More server CPU, but consistent rendering across all clients |
| **Rate limiting storage** | Redis | Fast but volatile — if Redis restarts, rate counters reset (acceptable: worst case = a few extra notifications) |
| **Provider abstraction** | Yes (pluggable adapters) | More code, but can switch providers without touching business logic |
| **Storage of notification body** | Store template + variables, not rendered body | Saves storage (1KB vs 50KB), but must re-render to view. Acceptable trade-off |
| **Push delivery confirmation** | APNS/FCM best-effort | Push delivery is not guaranteed by Apple/Google. Accept "sent" as success for push |
| **Multi-region** | Active-active for workers, single DB region | Workers run in nearest region (lower provider latency). DB is single-region with read replicas |

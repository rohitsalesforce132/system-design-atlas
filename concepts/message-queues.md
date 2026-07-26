# Message Queues & Asynchronous Processing

## What It Is (Analogy First)

Imagine a restaurant kitchen. If the chef has to cook each dish, plate it, and personally walk it to your table, they can only handle a few orders at a time. But with a **ticket rail** (order queue), the chef cooks dishes at their own pace and puts them on the counter for waiters to pick up when ready.

- **Chef** = Your application worker
- **Ticket rail** = Message queue
- **Waiter** = Consumer service that picks up the completed work

A message queue lets parts of your system work **asynchronously** — at their own pace, without blocking each other.

```
User clicks "Upload Video"
  │
  ▼
Web Server receives request
  │
  ├──► [MESSAGE QUEUE] ◄── stores "video:123 needs processing"
  │         │
  │    (returns immediately to user)
  │
  └──► User sees "Your video is processing, we'll notify you..."

Later, a worker picks up the message:
  [Worker] ──► picks up "video:123"
           ──► transcodes video (takes 10 minutes)
           ──► uploads to CDN
           └──► sends notification: "Your video is ready!"
```

## Why You Need Message Queues

### Problem: Synchronous Processing Blocks Users
```
User uploads video (2GB)
  ↓
Web Server transcodes video (10 minutes)
  ↓
User waits 10 minutes looking at a spinning spinner
  ↓
User gives up and leaves
```

### Solution: Queue It
```
User uploads video
  ↓
Web Server pushes message to queue (0.01 seconds)
  ↓
User sees "Processing..." immediately
  ↓
Worker processes video in background (10 minutes)
  ↓
Worker sends push notification when done
```

**Key benefits:**
1. **Decoupling:** Upload service doesn't need to know about transcoding service.
2. **Resilience:** If transcoding worker crashes, message stays in queue. Another worker picks it up.
3. **Scaling:** Add more workers during peak hours, remove them when quiet.
4. **Buffering:** Queue absorbs traffic spikes. Workers process at steady rate.
5. **Ordering:** Some queues guarantee message order (important for event sequences).

## Core Concepts

```
Producer ──► [ Queue ] ──► Consumer

Producer:    The system that creates messages (e.g., web server)
Queue:       Stores messages reliably until processed
Consumer:    Worker that picks up and processes messages
```

### Point-to-Point vs Pub/Sub

```
Point-to-Point (Task Queue):
  Producer ──► [Queue] ──► One consumer processes each message
                            Worker 1 gets job A
                            Worker 2 gets job B
                            Worker 3 gets job C

Pub/Sub (Publish/Subscribe):
  Producer ──► [Topic] ──► ALL subscribers get a copy
              │             Email Service gets event
              │             Analytics gets event
              │             Push Notification gets event
```

### At-Least-Once vs At-Most-Once vs Exactly-Once

| Delivery Guarantee | Behavior | Risk | Use Case |
|-------------------|----------|------|----------|
| **At-most-once** | Message may be lost, never duplicated | Data loss | Logging, metrics (losing some is OK) |
| **At-least-once** | Message is never lost, may be duplicated | Duplicate processing | Most apps (make consumers idempotent) |
| **Exactly-once** | Message processed exactly one time | Hard to achieve | Financial transactions |

**Exactly-once is extremely hard.** Most systems use at-least-once + idempotent consumers (processing the same message twice has no extra effect).

## Popular Message Queue Technologies

| Tool | Type | Strengths | Used By |
|------|------|-----------|---------|
| **Kafka** | Distributed log | Massive throughput, ordered messages, event streaming | LinkedIn, Netflix, Uber, Twitter |
| **RabbitMQ** | Traditional queue | Flexible routing, priority queues, small-medium scale | Many startups and enterprises |
| **SQS** | Cloud queue | Fully managed, infinite scale, simple | AWS-centric companies |
| **Redis Streams** | In-memory queue | Extremely fast, integrates with Redis | Instagram, Twitter |
| **NATS** | Lightweight pub/sub | Ultra-low latency, simple | Microservices |
| **Pulsar** | Hybrid (Kafka-like) | Multi-tenancy, separate storage and compute | Yahoo, Tencent |

## Deep Dive: Apache Kafka

Kafka is the most important message system to understand. It's not a traditional queue — it's a **distributed log**.

### Core Concept: The Log

Think of Kafka as an append-only log book:
```
Topic: "user-events"

Offset:  0    1    2    3    4    5    6    7
Event:  LOGIN CLICK CLICK PURCH LOGOUT LOGIN CLICK PURCH
                                ^
                                Current position of Consumer B
                    ^
                    Current position of Consumer A
```

- **Producers** append events to the end of the log.
- **Consumers** read from their own position (offset).
- **Events are not deleted** when consumed — they persist for days/weeks.
- Multiple consumers can read independently at different speeds.

### Partitions (How Kafka Scales)

```
Topic: "user-events" (3 partitions)

Partition 0: [msg0] [msg3] [msg6] [msg9]
Partition 1: [msg1] [msg4] [msg7] [msg10]
Partition 2: [msg2] [5] [msg8] [msg11]

Producer decides partition by: hash(key) % num_partitions
  e.g., user_id=42 → hash(42) % 3 = Partition 0
```

- Each partition is ordered.
- Messages with the same key always go to the same partition.
- More partitions = more parallelism (one consumer per partition).

### Consumer Groups

```
Topic: user-events (3 partitions)

Consumer Group "email-service":
  Consumer 1 reads Partition 0
  Consumer 2 reads Partition 1
  Consumer 3 reads Partition 2

Consumer Group "analytics-service":
  Consumer A reads Partition 0+1 (only 2 consumers in this group)
  Consumer B reads Partition 2
```

Each consumer group gets its own copy of all messages (pub/sub).
Within a group, partitions are split across consumers (point-to-point).

## Real-World Architecture Patterns

### Pattern 1: Video Processing Pipeline (YouTube-style)
```
User uploads video
  │
  ▼
Upload Service ──► Kafka topic: "video-uploaded"
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
     Thumbnail    Transcoder    Extract
     Generator    Worker        Metadata
          │             │             │
          ▼             ▌             ▼
     Upload to    Multiple         Store in
     S3/CDN       resolutions      DB
                        │
                        ▼
                  Kafka topic: "video-ready"
                        │
                        ▼
                  Notification Service
                  → Push notification to user
```

### Pattern 2: Real-Time Analytics (Uber-style)
```
Driver location update (every 4 seconds)
  │
  ▼
API Gateway ──► Kafka topic: "driver-locations"
                    │
          ┌─────────┼─────────────────┐
          ▼         ▼                 ▼
     Surge     Trip Matching      Analytics
     Pricing    (find riders)     (store for BI)
     Service    Service           Pipeline
          │         │
          ▼         ▼
     Update     Notify nearby
     prices     riders
```

### Pattern 3: Order Processing (Amazon-style)
```
User clicks "Place Order"
  │
  ▼
Order Service ──► Kafka topic: "order-created"
                       │
          ┌────────────┼────────────┬────────────┐
          ▼            ▼            ▼            ▼
     Inventory    Payment       Shipping      Notification
     Service      Service       Service       Service
     (reserve     (charge card) (schedule     (send email)
      items)                    pickup)
```

Each downstream service works independently. If payment service is slow, inventory and shipping aren't blocked.

## How Message Queues Enable Microservices

```
                    ┌──────────────┐
User ──► API Gateway ──► [Order Service] ──► Kafka ──► [Inventory] [Payment] [Shipping]
                    │                                            │
                    └──► [Auth Service]                          └──► Kafka ──► [Notification]
```

- Each service is independent (different teams, different deploy cycles).
- Services communicate via events, not direct HTTP calls.
- If one service crashes, others continue working.
- New services can subscribe to existing events without modifying producers.

## How YOU Can Build This

### Level 1: Redis as a Simple Queue
```python
import redis
import json

r = redis.Redis(host='localhost', port=6379)

# Producer: Push a message
r.lpush('task_queue', json.dumps({'task': 'resize_image', 'file': 'cat.jpg'}))

# Consumer: Pop a message (blocking)
while True:
    _, message = r.brpop('task_queue', timeout=0)
    task = json.loads(message)
    process_task(task)  # Your processing logic
```

### Level 2: RabbitMQ
```python
# Producer
import pika

connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
channel = connection.channel()
channel.queue_declare(queue='task_queue', durable=True)  # durable = survives restart

channel.basic_publish(
    exchange='',
    routing_key='task_queue',
    body='Process order #12345',
    properties=pika.BasicProperties(delivery_mode=2)  # persistent
)
print("Sent message")
connection.close()

# Consumer
def callback(ch, method, properties, body):
    print(f"Received: {body}")
    process(body)
    ch.basic_ack(delivery_tag=method.delivery_tag)  # acknowledge

channel.basic_consume(queue='task_queue', on_message_callback=callback)
channel.start_consuming()
```

### Level 3: Kafka (Production Grade)
```python
from kafka import KafkaProducer, KafkaConsumer
import json

# Producer
producer = KafkaProducer(
    bootstrap_servers=['localhost:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)
producer.send('user-events', {'user_id': 123, 'event': 'login'})
producer.flush()

# Consumer
consumer = KafkaConsumer(
    'user-events',
    bootstrap_servers=['bootstrap-server:9092'],
    group_id='email-service',
    auto_offset_reset='earliest',
    value_deserializer=lambda x: json.loads(x.decode('utf-8'))
)
for message in consumer:
    print(f"Received: {message.value}")
    process_event(message.value)
```

## Common Interview Questions

**Q: When should I use a message queue?**
A: Use a queue when:
1. An operation takes more than 200ms (video processing, report generation, ML inference).
2. You need to decouple services (producer doesn't depend on consumer being available).
3. You need to smooth out traffic spikes (queue absorbs the spike, workers process steadily).
4. You need reliability (message survives if consumer crashes mid-processing).

**Q: Kafka vs RabbitMQ — how to choose?**
A:
- **Kafka:** Event streaming, massive throughput (millions/sec), event sourcing, analytics pipelines. Messages persist for days.
- **RabbitMQ:** Task distribution, flexible routing, priority queues. Messages deleted after consumption.

**Q: What happens if a consumer crashes mid-processing?**
A: With acknowledgement: the consumer doesn't ACK the message. After a timeout (visibility timeout), the queue redelivers the message to another consumer. This is why consumers must be **idempotent** — processing the same message twice should have no extra effect.

**Q: How do you achieve exactly-once processing?**
A: True exactly-once is extremely hard. Practical approaches:
1. Use unique message IDs and track processed IDs in a database.
2. Combine the message acknowledgment and the database write in a single transaction.
3. Accept at-least-once and make consumers idempotent.
- **Kafka 0.11+ supports transactional exactly-once** between Kafka topics, but not for external systems (like databases).

**Q: How many consumers do I need?**
A: Match consumer count to message arrival rate and processing time. If messages arrive at 100/sec and each takes 1 second to process → need 100 consumers. Use auto-scaling to match load.

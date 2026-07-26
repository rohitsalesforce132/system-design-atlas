"""
NebulaShop Worker Service
Consumes Kafka events and:
  1. Indexes new posts into Elasticsearch (search)
  2. Sends real-time notifications via Redis Pub/Sub
  3. Updates analytics counters
"""
import json, time, os, sys
from confluent_kafka import Consumer
from elasticsearch import Elasticsearch
import redis

# ═══════════════════════════════════════════════════════════════
# CONNECTIONS
# ═══════════════════════════════════════════════════════════════

kafka_brokers = os.environ.get("KAFKA_BROKERS", "localhost:9092")
es = Elasticsearch(os.environ.get("ES_URL", "http://localhost:9200"))
r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

consumer = Consumer({
    "bootstrap.servers": kafka_brokers,
    "group.id": "nebula-worker",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})

# ═══════════════════════════════════════════════════════════════
# INIT: CREATE ES INDEX + KAFKA TOPICS
# ═══════════════════════════════════════════════════════════════

def init_elasticsearch():
    """Create the posts index if it doesn't exist."""
    if not es.indices.exists(index="posts"):
        es.indices.create(index="posts", body={
            "mappings": {
                "properties": {
                    "id": {"type": "integer"},
                    "user_id": {"type": "integer"},
                    "title": {"type": "text", "analyzer": "english"},
                    "content": {"type": "text", "analyzer": "english"},
                    "created_at": {"type": "date"}
                }
            }
        })
        print("[Worker] Created Elasticsearch index 'posts'", flush=True)
    else:
        print("[Worker] Elasticsearch index 'posts' already exists", flush=True)

def init_existing_posts():
    """Index all existing posts from PostgreSQL into Elasticsearch."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    pg = psycopg2.connect(
        os.environ.get("POSTGRES_URL", "postgresql://nebula:nebula123@localhost:5432/nebulashop"),
        cursor_factory=RealDictCursor
    )
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM posts")
        posts = cur.fetchall()
    pg.close()

    for post in posts:
        post_dict = dict(post)
        es.index(index="posts", id=post_dict["id"], body={
            "id": post_dict["id"],
            "user_id": post_dict["user_id"],
            "title": post_dict["title"],
            "content": post_dict["content"],
            "created_at": post_dict["created_at"].isoformat() if post_dict.get("created_at") else None,
        })
    if posts:
        print(f"[Worker] Indexed {len(posts)} existing posts into Elasticsearch", flush=True)
    es.indices.refresh(index="posts")

# ═══════════════════════════════════════════════════════════════
# EVENT HANDLERS
# ═══════════════════════════════════════════════════════════════

def handle_post_created(event):
    """Index a new post into Elasticsearch."""
    post = event["post"]
    es.index(
        index="posts",
        id=post["id"],
        body={
            "id": post["id"],
            "user_id": post["user_id"],
            "title": post["title"],
            "content": post["content"],
            "created_at": post.get("created_at"),
        }
    )
    es.indices.refresh(index="posts")
    print(f"[Worker] Indexed post {post['id']} in Elasticsearch: '{post['title']}'", flush=True)

    # Notify user's followers via Redis Pub/Sub (real-time feed update)
    r.publish("feed_updates", json.dumps(post, default=str))
    print(f"[Worker] Published feed update for post {post['id']}", flush=True)

    # Update analytics counter
    r.hincrby("analytics", "total_posts", 1)

def handle_order_created(event):
    """Process a new order: update analytics, send notification."""
    order = event["order"]
    print(f"[Worker] Processing order {order['id']}: {order['product']} x{order['quantity']}", flush=True)

    # Update analytics
    r.hincrby("analytics", "total_orders", 1)
    r.hincrbyfloat("analytics", "total_revenue", float(order["price"]) * int(order["quantity"]))

    # Publish notification to user's personal channel
    r.publish(f"user:{order['user_id']}", json.dumps({
        "type": "order_update",
        "message": f"Your order '{order['product']}' is confirmed!",
        "order_id": order["id"],
        "total": float(order["price"]) * int(order["quantity"])
    }, default=str))
    print(f"[Worker] Sent order confirmation to user {order['user_id']}", flush=True)

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    print("[Worker] Starting NebulaShop Worker Service...", flush=True)

    # Initialize Elasticsearch index + index existing posts
    init_elasticsearch()
    init_existing_posts()

    # Subscribe to Kafka topics
    topics = ["post-events", "order-events"]
    consumer.subscribe(topics)
    print(f"[Worker] Subscribed to Kafka topics: {topics}", flush=True)

    print("[Worker] Ready! Waiting for events...\n", flush=True)

    while True:
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            continue
        if msg.error():
            print(f"[Worker] Kafka error: {msg.error()}", flush=True)
            continue

        # Parse event
        event = json.loads(msg.value())
        event_type = event.get("event_type", "unknown")

        print(f"\n[Worker] Received event: {event_type} from partition {msg.partition()}", flush=True)

        # Route to handler
        try:
            if event_type == "post.created":
                handle_post_created(event)
            elif event_type == "order.created":
                handle_order_created(event)
            else:
                print(f"[Worker] Unknown event type: {event_type}", flush=True)

            # Commit offset after successful processing
            consumer.commit(msg)
            print(f"[Worker] Committed offset for {event_type}", flush=True)

        except Exception as e:
            print(f"[Worker] Error processing {event_type}: {e}", flush=True)
            # Don't commit — message will be re-delivered (at-least-once)

if __name__ == "__main__":
    main()

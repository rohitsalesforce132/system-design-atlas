"""
NebulaShop Worker — Native (No Docker)
Consumes Redis Streams (Kafka alternative) and:
  1. Indexes new posts into Elasticsearch
  2. Sends real-time notifications via Redis Pub/Sub
  3. Updates analytics counters
"""
import json, time, os, sys
from elasticsearch import Elasticsearch
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
es = None

def get_es():
    global es
    if es is None:
        try:
            es = Elasticsearch("http://localhost:9200")
            es.info()
            print(f"[Worker] Connected to Elasticsearch {es.info()['version']['number']}")
        except Exception as e:
            es = None
            print(f"[Worker] Elasticsearch not ready: {e}")
    return es

def init_elasticsearch():
    client = get_es()
    if not client:
        print("[Worker] Elasticsearch not available — search will use PostgreSQL fallback")
        return
    if not client.indices.exists(index="posts"):
        client.indices.create(index="posts", body={
            "mappings": {"properties": {
                "id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "title": {"type": "text", "analyzer": "english"},
                "content": {"type": "text", "analyzer": "english"},
                "created_at": {"type": "date"}
            }}
        })
        print("[Worker] Created Elasticsearch index 'posts'")
    else:
        print("[Worker] Elasticsearch index 'posts' already exists")

def index_existing_posts():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    client = get_es()
    if not client:
        return
    pg = psycopg2.connect("host=localhost port=5432 dbname=nebulashop user=nebula", cursor_factory=RealDictCursor)
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM posts")
        posts = cur.fetchall()
    pg.close()
    for post in posts:
        post_dict = dict(post)
        client.index(index="posts", id=post_dict["id"], body={
            "id": post_dict["id"],
            "user_id": post_dict["user_id"],
            "title": post_dict["title"],
            "content": post_dict["content"],
            "created_at": post_dict["created_at"].isoformat() if post_dict.get("created_at") else None,
        })
    if posts:
        client.indices.refresh(index="posts")
        print(f"[Worker] Indexed {len(posts)} existing posts into Elasticsearch")

def handle_post_created(event):
    post = event["post"]
    client = get_es()
    if client:
        client.index(index="posts", id=post["id"], body={
            "id": post["id"], "user_id": post["user_id"],
            "title": post["title"], "content": post["content"],
            "created_at": post.get("created_at"),
        })
        client.indices.refresh(index="posts")
        print(f"[Worker] Indexed post {post['id']} in Elasticsearch: '{post['title']}'")
    else:
        print(f"[Worker] ES not available — post {post['id']} not indexed")

    # Update analytics
    r.hincrby("analytics", "total_posts", 1)
    print(f"[Worker] Updated analytics: total_posts")

def handle_order_created(event):
    order = event["order"]
    print(f"[Worker] Processing order {order['id']}: {order['product']} x{order['quantity']}")
    r.hincrby("analytics", "total_orders", 1)
    r.hincrbyfloat("analytics", "total_revenue", float(order["price"]) * int(order["quantity"]))
    print(f"[Worker] Updated analytics: total_orders, total_revenue")

def main():
    print("[Worker] Starting NebulaShop Worker (Redis Streams mode)...")
    init_elasticsearch()
    index_existing_posts()

    # Create consumer group for Redis Streams
    try:
        r.xgroup_create("post-events", "nebula-worker", "$", mkstream=True)
        r.xgroup_create("order-events", "nebula-worker", "$", mkstream=True)
        print("[Worker] Created consumer groups")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            print("[Worker] Consumer groups already exist")
        else:
            raise

    print("[Worker] Ready! Waiting for events from Redis Streams...\n")

    while True:
        # Read from both streams
        results = r.xreadgroup(
            groupname="nebula-worker",
            consumername="worker-1",
            streams={"post-events": ">", "order-events": ">"},
            count=10,
            block=2000
        )

        if not results:
            continue

        for stream_name, messages in results:
            for msg_id, fields in messages:
                try:
                    event = json.loads(fields.get("event", "{}"))
                    event_type = event.get("event_type", "unknown")
                    print(f"\n[Worker] Received: {event_type} from {stream_name}")

                    if event_type == "post.created":
                        handle_post_created(event)
                    elif event_type == "order.created":
                        handle_order_created(event)
                    else:
                        print(f"[Worker] Unknown event: {event_type}")

                    # Acknowledge
                    r.xack(stream_name, "nebula-worker", msg_id)
                    print(f"[Worker] Ack'd {msg_id}")
                except Exception as e:
                    print(f"[Worker] Error: {e}")

if __name__ == "__main__":
    main()

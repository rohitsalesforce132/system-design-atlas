"""
NebulaShop API Server
Demonstrates: PostgreSQL, Redis cache, Elasticsearch search, Kafka events, MinIO storage
"""
import json, time, os
from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
from elasticsearch import Elasticsearch
from confluent_kafka import Producer
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════
# CONNECTIONS
# ═══════════════════════════════════════════════════════════════

# PostgreSQL (primary database)
pg = psycopg2.connect(
    os.environ.get("POSTGRES_URL", "postgresql://nebula:nebula123@localhost:5432/nebulashop"),
    cursor_factory=RealDictCursor
)

# Redis (cache + rate limiter + pub/sub)
r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

# Elasticsearch (full-text search)
es = Elasticsearch(os.environ.get("ES_URL", "http://localhost:9200"))

# Kafka Producer (event streaming)
kafka_producer = Producer({"bootstrap.servers": os.environ.get("KAFKA_BROKERS", "localhost:9092")})

# ═══════════════════════════════════════════════════════════════
# PROMETHEUS METRICS
# ═══════════════════════════════════════════════════════════════

REQUEST_COUNT = Counter("nebula_api_requests_total", "Total API requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("nebula_api_request_duration_seconds", "API request latency", ["endpoint"])
CACHE_HITS = Counter("nebula_cache_hits_total", "Cache hits", ["resource"])
CACHE_MISSES = Counter("nebula_cache_misses_total", "Cache misses", ["resource"])
KAFKA_EVENTS = Counter("nebula_kafka_events_total", "Kafka events published", ["event_type"])

# ═══════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    latency = time.time() - getattr(request, "start_time", time.time())
    REQUEST_COUNT.labels(request.method, request.path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.path).observe(latency)
    return response

# ═══════════════════════════════════════════════════════════════
# HEALTH & METRICS
# ═══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "nebula-api"})

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

# ═══════════════════════════════════════════════════════════════
# USER ENDPOINTS (PostgreSQL + Redis Cache)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/users", methods=["POST"])
def create_user():
    data = request.json
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, email, bio) VALUES (%s, %s, %s) RETURNING *",
            (data["username"], data["email"], data.get("bio", ""))
        )
        pg.commit()
        user = cur.fetchone()
    # Invalidate cache
    r.delete(f"user:{user['id']}")
    return jsonify(dict(user)), 201

@app.route("/api/users/<int:user_id>")
def get_user(user_id):
    cache_key = f"user:{user_id}"

    # Try Redis cache first
    cached = r.get(cache_key)
    if cached:
        CACHE_HITS.labels("user").inc()
        return jsonify(json.loads(cached))

    # Cache miss → query PostgreSQL
    CACHE_MISSES.labels("user").inc()
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    if not user:
        return jsonify({"error": "User not found"}), 404

    user_dict = dict(user)
    # Cache for 5 minutes
    r.setex(cache_key, 300, json.dumps(user_dict, default=str))
    return jsonify(user_dict)

# ═══════════════════════════════════════════════════════════════
# POST ENDPOINTS (PostgreSQL + Kafka + Elasticsearch)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/posts", methods=["POST"])
def create_post():
    data = request.json
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (user_id, title, content) VALUES (%s, %s, %s) RETURNING *",
            (data["user_id"], data["title"], data["content"])
        )
        pg.commit()
        post = cur.fetchone()

    post_dict = dict(post)

    # Publish event to Kafka (worker will index in ES + send notification)
    event = {"event_type": "post.created", "post": post_dict}
    kafka_producer.produce(
        "post-events",
        key=str(post_dict["user_id"]),
        value=json.dumps(event, default=str)
    )
    kafka_producer.flush()
    KAFKA_EVENTS.labels("post.created").inc()

    # Redis Pub/Sub: notify realtime server for live feed push
    r.publish("feed_updates", json.dumps(post_dict, default=str))

    return jsonify(post_dict), 201

@app.route("/api/posts")
def list_posts():
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT 50")
        posts = cur.fetchall()
    return jsonify([dict(p) for p in posts])

# ═══════════════════════════════════════════════════════════════
# SEARCH ENDPOINT (Elasticsearch)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/search")
def search():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    # Search Elasticsearch
    result = es.search(
        index="posts",
        body={
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "content"]
                }
            },
            "size": 20,
            "sort": [{"_score": "desc"}, {"created_at": "desc"}]
        }
    )

    hits = [
        {
            "id": hit["_source"].get("id"),
            "title": hit["_source"].get("title"),
            "content": hit["_source"].get("content", "")[:200],
            "user_id": hit["_source"].get("user_id"),
            "score": hit["_score"],
        }
        for hit in result["hits"]["hits"]
    ]

    return jsonify({"query": query, "total": len(hits), "results": hits})

# ═══════════════════════════════════════════════════════════════
# ORDER ENDPOINTS (PostgreSQL + Kafka + Redis Pub/Sub)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/orders", methods=["POST"])
def create_order():
    data = request.json
    with pg.cursor() as cur:
        cur.execute(
            """INSERT INTO orders (user_id, product, quantity, price, status)
               VALUES (%s, %s, %s, %s, 'confirmed') RETURNING *""",
            (data["user_id"], data["product"], data["quantity"], data["price"])
        )
        pg.commit()
        order = cur.fetchone()

    order_dict = dict(order)

    # Publish to Kafka (worker processes order: inventory, notification, analytics)
    event = {"event_type": "order.created", "order": order_dict}
    kafka_producer.produce(
        "order-events",
        key=str(order_dict["user_id"]),
        value=json.dumps(event, default=str)
    )
    kafka_producer.flush()
    KAFKA_EVENTS.labels("order.created").inc()

    # Notify user in real-time
    r.publish(f"user:{order_dict['user_id']}", json.dumps({
        "type": "order_confirmation",
        "message": f"Order confirmed: {order_dict['product']} x{order_dict['quantity']}",
        "order_id": order_dict["id"]
    }, default=str))

    return jsonify(order_dict), 201

@app.route("/api/orders")
def list_orders():
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50")
        orders = cur.fetchall()
    return jsonify([dict(o) for o in orders])

# ═══════════════════════════════════════════════════════════════
# RATE LIMITING DEMONSTRATION (Redis)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/rate-limited")
def rate_limited():
    """Demonstrates Redis-based rate limiting (token bucket style)."""
    client_ip = request.remote_addr or "unknown"
    minute_key = f"ratelimit:{client_ip}:{int(time.time() // 60)}"

    current = r.incr(minute_key)
    if current == 1:
        r.expire(minute_key, 60)

    limit = 10  # 10 requests per minute
    if current > limit:
        return jsonify({
            "error": "Rate limit exceeded",
            "limit": limit,
            "retry_after": 60 - int(time.time() % 60)
        }), 429

    return jsonify({
        "message": "Request allowed",
        "remaining": limit - current,
        "limit": limit
    })

# ═══════════════════════════════════════════════════════════════
# CACHE STATS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/stats")
def stats():
    """Show Redis cache stats and DB connection info."""
    info = r.info("memory")
    return jsonify({
        "redis_used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
        "redis_connected_clients": r.info("clients").get("connected_clients", 0),
        "redis_total_keys": r.dbsize(),
        "es_cluster_status": es.info()["status"],
        "kafka_topics": "post-events, order-events"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

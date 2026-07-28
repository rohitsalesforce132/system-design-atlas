"""
NebulaShop API Server — Native (No Docker)
Uses: PostgreSQL, Redis, Elasticsearch, Redis Streams (Kafka alternative)
"""
import json, time, os
from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
from elasticsearch import Elasticsearch
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════
# CONNECTIONS (local — no Docker)
# ═══════════════════════════════════════════════════════════════

pg = psycopg2.connect(
    "host=localhost port=5432 dbname=nebulashop user=nebula",
    cursor_factory=RealDictCursor
)
pg.autocommit = True  # Each statement auto-commits; no transaction state issues
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# Elasticsearch (may not be ready yet — lazy connect)
es = None
def get_es():
    global es
    if es is None:
        try:
            es = Elasticsearch("http://localhost:9200")
            es.info()
        except Exception:
            es = None
    return es

# ═══════════════════════════════════════════════════════════════
# PROMETHEUS METRICS
# ═══════════════════════════════════════════════════════════════

REQUEST_COUNT = Counter("nebula_api_requests_total", "Total API requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("nebula_api_request_duration_seconds", "API request latency", ["endpoint"])
CACHE_HITS = Counter("nebula_cache_hits_total", "Cache hits", ["resource"])
CACHE_MISSES = Counter("nebula_cache_misses_total", "Cache misses", ["resource"])
STREAM_EVENTS = Counter("nebula_stream_events_total", "Stream events published", ["event_type"])

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
    services = {"api": "ok", "postgres": "ok", "redis": "ok"}
    try:
        with pg.cursor() as cur:
            cur.execute("SELECT 1")
            services["postgres"] = "ok"
    except:
        services["postgres"] = "error"
    try:
        r.ping()
        services["redis"] = "ok"
    except:
        services["redis"] = "error"
    es_client = get_es()
    services["elasticsearch"] = "ok" if es_client else "not_ready"
    return jsonify({"status": "ok", "services": services})

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

# ═══════════════════════════════════════════════════════════════
# USERS (PostgreSQL + Redis Cache)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/users", methods=["POST"])
def create_user():
    data = request.json
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, email, bio) VALUES (%s, %s, %s) RETURNING *",
            (data["username"], data["email"], data.get("bio", ""))
        )
        user = cur.fetchone()
    r.delete(f"user:{user['id']}")
    user_dict = dict(user)
    user_dict["created_at"] = str(user_dict.get("created_at", ""))
    return jsonify(user_dict), 201

@app.route("/api/users/<int:user_id>")
def get_user(user_id):
    cache_key = f"user:{user_id}"
    cached = r.get(cache_key)
    if cached:
        CACHE_HITS.labels("user").inc()
        return jsonify(json.loads(cached))
    CACHE_MISSES.labels("user").inc()
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404
    user_dict = dict(user)
    r.setex(cache_key, 300, json.dumps(user_dict, default=str))
    return jsonify(user_dict)

# ═══════════════════════════════════════════════════════════════
# POSTS (PostgreSQL + Redis Streams + Redis Pub/Sub)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/posts", methods=["POST"])
def create_post():
    data = request.json
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (user_id, title, content) VALUES (%s, %s, %s) RETURNING *",
            (data["user_id"], data["title"], data["content"])
        )
        post = cur.fetchone()

    post_dict = dict(post)
    post_dict["created_at"] = str(post_dict.get("created_at", ""))

    # Publish to Redis Stream (Kafka alternative for local dev)
    r.xadd("post-events", {"event": json.dumps({"event_type": "post.created", "post": post_dict}, default=str)})

    # Redis Pub/Sub: notify realtime server for live feed
    r.publish("feed_updates", json.dumps(post_dict, default=str))

    STREAM_EVENTS.labels("post.created").inc()
    return jsonify(post_dict), 201

@app.route("/api/posts")
def list_posts():
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT 50")
        posts = cur.fetchall()
    return jsonify([dict(p, created_at=str(p.get("created_at", ""))) for p in posts])

# ═══════════════════════════════════════════════════════════════
# SEARCH (Elasticsearch with fallback to PostgreSQL)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/search")
def search():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    es_client = get_es()
    if es_client:
        try:
            result = es_client.search(
                index="posts",
                body={
                    "query": {"multi_match": {"query": query, "fields": ["title^2", "content"]}},
                    "size": 20,
                    "sort": [{"_score": "desc"}]
                }
            )
            hits = [
                {
                    "id": h["_source"].get("id"),
                    "title": h["_source"].get("title"),
                    "content": h["_source"].get("content", "")[:200],
                    "user_id": h["_source"].get("user_id"),
                    "score": round(h["_score"], 2),
                }
                for h in result["hits"]["hits"]
            ]
            return jsonify({"engine": "elasticsearch", "query": query, "total": len(hits), "results": hits})
        except Exception as e:
            pass  # Fall through to PostgreSQL fallback

    # PostgreSQL ILIKE fallback (no ES available)
    with pg.cursor() as cur:
        cur.execute(
            "SELECT * FROM posts WHERE title ILIKE %s OR content ILIKE %s ORDER BY created_at DESC LIMIT 20",
            (f"%{query}%", f"%{query}%")
        )
        posts = cur.fetchall()
    return jsonify({
        "engine": "postgresql_ilike",
        "query": query,
        "total": len(posts),
        "results": [dict(p, created_at=str(p.get("created_at", ""))) for p in posts]
    })

# ═══════════════════════════════════════════════════════════════
# ORDERS (PostgreSQL + Redis Streams + Pub/Sub)
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
        order = cur.fetchone()

    order_dict = dict(order)
    order_dict["created_at"] = str(order_dict.get("created_at", ""))

    # Publish to Redis Stream
    r.xadd("order-events", {"event": json.dumps({"event_type": "order.created", "order": order_dict}, default=str)})

    # Real-time notification
    r.publish(f"user:{order_dict['user_id']}", json.dumps({
        "type": "order_confirmation",
        "message": f"Order confirmed: {order_dict['product']} x{order_dict['quantity']}",
        "order_id": order_dict["id"]
    }, default=str))

    STREAM_EVENTS.labels("order.created").inc()
    return jsonify(order_dict), 201

@app.route("/api/orders")
def list_orders():
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50")
        orders = cur.fetchall()
    return jsonify([dict(o, created_at=str(o.get("created_at", ""))) for o in orders])

# ═══════════════════════════════════════════════════════════════
# RATE LIMITING (Redis)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/rate-limited")
def rate_limited():
    client_ip = request.remote_addr or "unknown"
    minute_key = f"ratelimit:{client_ip}:{int(time.time() // 60)}"
    current = r.incr(minute_key)
    if current == 1:
        r.expire(minute_key, 60)
    limit = 10
    if current > limit:
        return jsonify({"error": "Rate limit exceeded", "limit": limit, "retry_after": 60 - int(time.time() % 60)}), 429
    return jsonify({"message": "Request allowed", "remaining": limit - current, "limit": limit})

# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/stats")
def stats():
    info = r.info("memory")
    analytics = r.hgetall("analytics") or {}
    return jsonify({
        "redis_used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
        "redis_total_keys": r.dbsize(),
        "redis_pubsub_channels": len(r.pubsub_channels()),
        "analytics": analytics,
        "es_status": "connected" if get_es() else "not_ready",
        "streams": {"post-events": r.xlen("post-events"), "order-events": r.xlen("order-events")}
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

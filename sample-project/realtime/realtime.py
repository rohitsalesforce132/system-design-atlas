"""
NebulaShop Realtime Server
Demonstrates: WebSocket + Redis Pub/Sub message bus

  Browser ──WebSocket──► Realtime Server ──► Redis Pub/Sub
                                              (receives messages
                                               from API server and worker)
"""
import asyncio, json, os
import websockets
import redis.asyncio as aioredis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Track connected clients: user_id → WebSocket connection
connected_clients = {}


async def handle_client(websocket):
    """Handle a single WebSocket client connection."""
    try:
        # First message from client should be their user_id
        auth_msg = await websocket.recv()
        auth = json.loads(auth_msg)

        if auth.get("type") != "auth":
            await websocket.send(json.dumps({"error": "Send auth first"}))
            return

        user_id = str(auth.get("user_id", "anonymous"))
        connected_clients[user_id] = websocket

        await websocket.send(json.dumps({
            "type": "connected",
            "message": f"Connected as user {user_id}. Listening for updates..."
        }))
        print(f"[Realtime] User {user_id} connected. Total: {len(connected_clients)}")

        # Keep connection alive, listen for client messages
        async for message in websocket:
            msg = json.loads(message)
            if msg.get("type") == "ping":
                await websocket.send(json.dumps({"type": "pong"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # Remove from connected clients
        user_id = [k for k, v in connected_clients.items() if v == websocket]
        for uid in user_id:
            del connected_clients[uid]
            print(f"[Realtime] User {uid} disconnected. Total: {len(connected_clients)}")


async def listen_to_redis():
    """Listen for messages from Redis Pub/Sub and push to WebSocket clients."""
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()

    # Subscribe to all relevant channels
    await pubsub.subscribe("feed_updates", "user:1", "user:2", "user:3")

    # Also subscribe to any user:* channel dynamically
    # (In production, use pattern subscribe: PSUBSCRIBE user:*)

    print("[Realtime] Listening to Redis Pub/Sub channels: feed_updates, user:1, user:2, user:3")

    async for message in pubsub.listen():
        if message["type"] not in ("message", "pmessage"):
            continue

        channel = message["channel"]
        data = message["data"]

        print(f"[Realtime] Received from Redis channel '{channel}': {data[:100]}...")

        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            msg = {"type": "raw", "data": data}

        # Route to appropriate clients
        if channel == "feed_updates":
            # Broadcast to ALL connected clients (new post in feed)
            await broadcast_to_all({
                "type": "new_post",
                "post": msg
            })

        elif channel.startswith("user:"):
            # Send to specific user
            user_id = channel.split(":")[1]
            await send_to_user(user_id, msg)


async def broadcast_to_all(message):
    """Send a message to all connected WebSocket clients."""
    if not connected_clients:
        return

    msg_str = json.dumps(message, default=str)
    disconnected = []

    for user_id, ws in connected_clients.items():
        try:
            await ws.send(msg_str)
        except websockets.exceptions.ConnectionClosed:
            disconnected.append(user_id)

    for uid in disconnected:
        del connected_clients[uid]


async def send_to_user(user_id, message):
    """Send a message to a specific user's WebSocket connection."""
    ws = connected_clients.get(str(user_id))
    if ws:
        try:
            await ws.send(json.dumps(message, default=str))
            print(f"[Realtime] Delivered message to user {user_id}")
        except websockets.exceptions.ConnectionClosed:
            del connected_clients[str(user_id)]
    else:
        # User not connected — in production, store for later delivery
        pass


async def heartbeat():
    """Send periodic ping to all clients to keep connections alive."""
    while True:
        await asyncio.sleep(30)
        disconnected = []
        for user_id, ws in connected_clients.items():
            try:
                await ws.send(json.dumps({"type": "heartbeat", "timestamp": asyncio.get_event_loop().time()}))
            except websockets.exceptions.ConnectionClosed:
                disconnected.append(user_id)
        for uid in disconnected:
            del connected_clients[uid]


async def main():
    """Start WebSocket server and Redis listener concurrently."""
    print("[Realtime] Starting NebulaShop Realtime Server on port 8765...")

    # Start Redis Pub/Sub listener (background task)
    redis_task = asyncio.create_task(listen_to_redis())

    # Start heartbeat (background task)
    heartbeat_task = asyncio.create_task(heartbeat())

    # Start WebSocket server
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        print("[Realtime] WebSocket server ready on ws://0.0.0.0:8765")
        print("[Realtime] Waiting for connections...\n")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())

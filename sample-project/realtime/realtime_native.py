"""
NebulaShop Realtime Server — Native (No Docker)
WebSocket server + Redis Pub/Sub for real-time updates
"""
import asyncio, json
import websockets
import redis.asyncio as aioredis

connected_clients = {}

async def handle_client(websocket):
    try:
        auth_msg = await websocket.recv()
        auth = json.loads(auth_msg)
        if auth.get("type") != "auth":
            await websocket.send(json.dumps({"error": "Send auth first"}))
            return
        user_id = str(auth.get("user_id", "anonymous"))
        connected_clients[user_id] = websocket
        await websocket.send(json.dumps({"type": "connected", "message": f"Connected as user {user_id}"}))
        print(f"[Realtime] User {user_id} connected. Total: {len(connected_clients)}")
        async for message in websocket:
            msg = json.loads(message)
            if msg.get("type") == "ping":
                await websocket.send(json.dumps({"type": "pong"}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        for uid in [k for k, v in connected_clients.items() if v == websocket]:
            del connected_clients[uid]
            print(f"[Realtime] User {uid} disconnected. Total: {len(connected_clients)}")

async def listen_to_redis():
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.psubscribe("feed_updates", "user:*")
    print("[Realtime] Listening to Redis Pub/Sub: feed_updates, user:*")
    async for message in pubsub.listen():
        if message["type"] not in ("message", "pmessage"):
            continue
        channel = message["channel"]
        data = message["data"]
        print(f"[Realtime] Received on '{channel}': {data[:80]}...")
        try:
            msg = json.loads(data)
        except:
            msg = {"type": "raw", "data": data}

        if channel == "feed_updates":
            await broadcast_to_all({"type": "new_post", "post": msg})
        elif channel.startswith("user:"):
            user_id = channel.split(":")[1]
            await send_to_user(user_id, msg)

async def broadcast_to_all(message):
    if not connected_clients:
        return
    msg_str = json.dumps(message, default=str)
    for uid in list(connected_clients.keys()):
        try:
            await connected_clients[uid].send(msg_str)
        except:
            del connected_clients[uid]

async def send_to_user(user_id, message):
    ws = connected_clients.get(str(user_id))
    if ws:
        try:
            await ws.send(json.dumps(message, default=str))
        except:
            del connected_clients[str(user_id)]

async def main():
    print("[Realtime] Starting on port 8765...")
    redis_task = asyncio.create_task(listen_to_redis())
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        print("[Realtime] WebSocket server ready on ws://localhost:8765")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

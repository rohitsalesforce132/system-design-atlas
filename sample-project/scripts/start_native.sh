#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# NebulaShop — Native Start Script (No Docker Required)
# ═══════════════════════════════════════════════════════════════
# Prerequisites: Python 3.12, PostgreSQL, Redis, pip packages
#
# This script starts:
#   1. PostgreSQL (if not running)
#   2. Redis (if not running)
#   3. API Server (Flask, port 5000)
#   4. Worker (Redis Streams consumer)
#   5. Realtime Server (WebSocket, port 8765)
# ═══════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."
PROJECT_DIR=$(pwd)
PYTHON=/home/rohit/neo4j-graphrag/venv/bin/python
PIP=/home/rohit/neo4j-graphrag/venv/bin/pip

echo "═══════════════════════════════════════════════════"
echo "  NebulaShop — Native Startup"
echo "═══════════════════════════════════════════════════"

# ── 1. Install Python deps ──────────────────────────────────
echo "📦 Installing Python dependencies..."
$PIP install -q flask psycopg2-binary redis elasticsearch prometheus-client websockets 2>/dev/null || true

# ── 2. Start Redis ──────────────────────────────────────────
if ! redis-cli ping > /dev/null 2>&1; then
    echo "🔴 Starting Redis..."
    redis-server --daemonize yes --port 6379
    sleep 1
fi
echo "✅ Redis: $(redis-cli ping)"

# ── 3. Start PostgreSQL ─────────────────────────────────────
if ! psql -h localhost -p 5432 -U nebula -d nebulashop -c "SELECT 1" > /dev/null 2>&1; then
    echo "🔴 Starting PostgreSQL..."
    export PGDATA=/tmp/nebula-pgdata
    if [ ! -d "$PGDATA" ]; then
        /home/rohit/.linuxbrew/bin/initdb -D "$PGDATA" -U nebula --auth=trust
    fi
    /home/rohit/.linuxbrew/bin/pg_ctl -D "$PGDATA" -l /tmp/nebula-pg.log -o "-p 5432" start
    sleep 2
    psql -h localhost -p 5432 -U nebula -d postgres -c "CREATE DATABASE nebulashop;" 2>/dev/null || true
    psql -h localhost -p 5432 -U nebula -d nebulashop -f "$PROJECT_DIR/scripts/init.sql" 2>/dev/null
fi
echo "✅ PostgreSQL: $(psql -h localhost -p 5432 -U nebula -d nebulashop -tAc 'SELECT version()' | head -c 40)..."

# ── 4. Start Worker ─────────────────────────────────────────
echo "🔴 Starting Worker (Redis Streams consumer)..."
$PYTHON "$PROJECT_DIR/worker/worker_native.py" > /tmp/nebula-worker.log 2>&1 &
echo $! > /tmp/nebula-worker.pid
sleep 2
echo "✅ Worker started (PID: $(cat /tmp/nebula-worker.pid))"

# ── 5. Start Realtime Server ────────────────────────────────
echo "🔴 Starting Realtime WebSocket server..."
$PYTHON "$PROJECT_DIR/realtime/realtime_native.py" > /tmp/nebula-realtime.log 2>&1 &
echo $! > /tmp/nebula-realtime.pid
sleep 2
echo "✅ Realtime started (PID: $(cat /tmp/nebula-realtime.pid))"

# ── 6. Start API Server ─────────────────────────────────────
echo "🔴 Starting API Server on port 5000..."
$PYTHON "$PROJECT_DIR/api/app_native.py" &
echo $! > /tmp/nebula-api.pid
sleep 3
echo "✅ API started (PID: $(cat /tmp/nebula-api.pid))"

# ── 7. Health Check ─────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo "  Health Check"
echo "═══════════════════════════════════════════════════"
sleep 2
curl -s http://localhost:5000/health | $PYTHON -m json.tool 2>/dev/null || echo "API not ready yet..."

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ NebulaShop is running!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  API:        http://localhost:5000"
echo "  WebSocket:  ws://localhost:8765"
echo "  Health:     http://localhost:5000/health"
echo "  Stats:      http://localhost:5000/api/stats"
echo ""
echo "  Logs:"
echo "    API:      tail -f /tmp/nebula-api.log"
echo "    Worker:   tail -f /tmp/nebula-worker.log"
echo "    Realtime: tail -f /tmp/nebula-realtime.log"
echo ""
echo "  Stop all:   kill \$(cat /tmp/nebula-*.pid)"
echo ""

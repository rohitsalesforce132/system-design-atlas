#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# NebulaShop Initialization Script
# Creates Kafka topics and Elasticsearch index
# ═══════════════════════════════════════════════════════════════

set -e

echo "═══════════════════════════════════════════════════"
echo "  NebulaShop — Initializing Infrastructure"
echo "═══════════════════════════════════════════════════"

# Wait for Redpanda (Kafka) to be ready
echo "⏳ Waiting for Redpanda (Kafka)..."
sleep 10

# Create Kafka topics
echo "📦 Creating Kafka topics..."
docker exec -it $(docker ps -qf "name=redpanda") rpk topic create post-events --partitions 3 --replicas 1 || true
docker exec -it $(docker ps -qf "name=redpanda") rpk topic create order-events --partitions 3 --replicas 1 || true

echo "✅ Kafka topics created: post-events, order-events"

# Create MinIO bucket
echo "📦 Creating MinIO bucket..."
docker exec -it $(docker ps -qf "name=minio") mc alias set local http://localhost:9000 minioadmin minioadmin || true
docker exec -it $(docker ps -qf "name=minio") mc mb local/nebulashop-uploads --ignore-existing || true

echo "✅ MinIO bucket created: nebulashop-uploads"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ NebulaShop is ready!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Web App:        http://localhost:8080"
echo "  API:            http://localhost:8080/api"
echo "  Grafana:        http://localhost:3001  (admin/admin)"
echo "  Prometheus:     http://localhost:9091"
echo "  MinIO Console:  http://localhost:9001"
echo ""

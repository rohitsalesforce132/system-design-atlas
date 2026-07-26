# Kubernetes — The Complete Deep Dive

> Kubernetes runs the infrastructure for Google, Spotify, Shopify, OpenAI, and most modern tech companies. This guide covers what it actually does, from pod scheduling to service networking to auto-scaling.

---

## Table of Contents

1. [The Problem Kubernetes Solves](#the-problem)
2. [Architecture — Control Plane & Worker Nodes](#architecture)
3. [Pods — The Atom of Kubernetes](#pods)
4. [Controllers — Deployments, StatefulSets, DaemonSets](#controllers)
5. [Services — How Networking Works](#services)
6. [Storage — Volumes, PVCs, StorageClasses](#storage)
7. [Configuration — ConfigMaps & Secrets](#config)
8. [Auto-Scaling — HPA, VPA, Cluster Autoscaler](#autoscaling)
9. [Scheduling — How Pods Are Placed on Nodes](#scheduling)
10. [Helm & Operators](#helm)
11. [How Real Companies Use K8s](#real-apps)
12. [How YOU Can Build This](#build)

---

<a id="the-problem"></a>
## The Problem Kubernetes Solves

### The Evolution of Deployment

```
PHASE 1: BARE METAL (1990s)
  "Buy a server. Install OS. Install your app. Hope it doesn't crash."
  → Months to provision. No scaling. No redundancy.

PHASE 2: VIRTUAL MACHINES (2000s)
  "Create VM. Install OS. Install app. Snapshot. Clone."
  → Minutes to provision. Can migrate. Still heavyweight (full OS per app).

PHASE 3: CONTAINERS (2010s)
  "Build Docker image. Run it anywhere. Start in seconds."
  → Lightweight (share OS kernel). Fast startup. Consistent environment.

  Problem: Now you have 1,000 containers.
  → Which machine runs which container?
  → What if a machine dies?
  → How do containers find each other?
  → How do you scale up/down?
  → How do you roll out a new version without downtime?

PHASE 4: ORCHESTRATION (Kubernetes)
  "Describe WHAT you want (3 replicas of my app). K8s figures out HOW."
  → Automated scheduling, scaling, healing, load balancing, rolling updates.
```

### What Kubernetes Actually Does

```
You say:                     Kubernetes does:
────────────────────────────────────────────────────────────
"I want 5 instances of       → Finds 5 suitable nodes
 my web app running"          → Pulls Docker image
                              → Starts 5 containers
                              → Monitors them
                              → If one dies → restarts it
                              → If a node dies → reschedules

"I want to update to          → Starts new version alongside old
 version 2.0"                 → Gradually shifts traffic
                              → Stops old containers one by one
                              → If something breaks → rolls back

"Scale to handle more         → Monitors CPU/memory
 traffic"                     → Adds more containers automatically
                              → When traffic drops → removes containers

"My database needs           → Attaches a disk
 persistent storage"          → If container moves → disk follows
```

---

<a id="architecture"></a>
## Architecture — Control Plane & Worker Nodes

### High-Level View

```
┌─────────────────────────────────────────────────────────────┐
│                   KUBERNETES CLUSTER                        │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              CONTROL PLANE (Brain)                    │   │
│  │                                                       │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │   │
│  │  │ API Server│  │Scheduler │  │ Controller Manager│   │   │
│  │  │           │  │          │  │                    │   │   │
│  │  │ All       │  │ Decides  │  │ Watches state,    │   │   │
│  │  │ components│  │ which    │  │ makes changes to  │   │   │
│  │  │ talk      │  │ node runs│  │ match desired     │   │   │
│  │  │ through   │  │ each pod │  │ state             │   │   │
│  │  │ this      │  │          │  │                    │   │   │
│  │  └──────────┘  └──────────┘  └──────────────────┘   │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────┐                │   │
│  │  │            etcd                    │                │   │
│  │  │  (The brain's memory — stores all  │                │   │
│  │  │   cluster state: what pods exist,  │                │   │
│  │  │   where they run, configs, etc.)   │                │   │
│  │  │  Distributed key-value store       │                │   │
│  │  │  with strong consistency (Raft)    │                │   │
│  │  └──────────────────────────────────┘                │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐              │
│  │WORKER NODE1│  │WORKER NODE2│  │WORKER NODE3│              │
│  │            │  │            │  │            │              │
│  │ ┌────────┐│  │ ┌────────┐ │  │ ┌────────┐ │              │
│  │ │kubelet ││  │ │kubelet │ │  │ │kubelet │ │  ← Agent that│
│  │ │        ││  │ │        │ │  │ │        │ │    manages   │
│  │ │Reports ││  │ │Reports │ │  │ │Reports │ │    pods on   │
│  │ │to API  ││  │ │to API  │ │  │ │to API  │ │    this node │
│  │ │server  ││  │ │server  │ │  │ │server  │ │              │
│  │ └────────┘│  │ └────────┘ │  │ └────────┘ │              │
│  │           │  │            │  │            │              │
│  │ ┌────────┐│  │ ┌────────┐ │  │ ┌────────┐ │              │
│  │ │kube-   ││  │ │kube-   │ │  │ │kube-   │ │  ← Network   │
│  │ │proxy   ││  │ │proxy   │ │  │ │proxy   │ │    proxy +   │
│  │ │        ││  │ │        │ │  │ │        │ │    iptables  │
│  │ │Manages ││  │ │Manages │ │  │ │Manages │ │    rules     │
│  │ │network ││  │ │network │ │  │ │network │ │              │
│  │ │routing ││  │ │routing │ │  │ │routing │ │              │
│  │ └────────┘│  │ └────────┘ │  │ └────────┘ │              │
│  │           │  │            │  │            │              │
│  │ ┌────────┐│  │ ┌────────┐ │  │ ┌────────┐ │              │
│  │ │container││  │ │container│ │  │ │container│ │              │
│  │ │runtime ││  │ │runtime │ │  │ │runtime │ │  ← Docker /   │
│  │ │(Docker/││  │ │(Docker/│ │  │ │(Docker/│ │    containerd │
│  │ │containerd)│ │containerd)│ │containerd)│ │              │
│  │ └───┬───┘│  │ └───┬────┘ │  │ └───┬────┘ │              │
│  │     │     │  │     │      │  │     │      │              │
│  │  ┌──▼──┐  │  │  ┌──▼──┐   │  │  ┌──▼──┐   │              │
│  │  │PODS │  │  │  │PODS │   │  │  │PODS │   │              │
│  │  │[][] │  │  │  │[][] │   │  │  │[][] │   │              │
│  │  └─────┘  │  │  └─────┘   │  │  └─────┘   │              │
│  └───────────┘  └───────────┘  └───────────┘              │
└─────────────────────────────────────────────────────────────┘
```

### Control Plane Components

#### API Server — The Front Door

```
ALL communication goes through the API Server:
  kubectl → API Server → etcd
  kubelet → API Server → etcd
  scheduler → API Server → etcd

  It's the ONLY component that reads/writes etcd directly.

  REST API:
    kubectl get pods
    → GET /api/v1/pods

    kubectl apply -f deployment.yaml
    → POST /apis/apps/v1/deployments
```

#### etcd — The Source of Truth

```
  etcd stores EVERYTHING about the cluster:
    - What pods exist and their status
    - Which node each pod runs on
    - ConfigMaps, Secrets
    - Service definitions
    - Resource quotas

  etcd uses Raft consensus for strong consistency.
  → 3 or 5 etcd nodes for quorum
  → If 1 of 3 dies → cluster still operational
  → If 2 of 3 die → cluster read-only (can't accept changes)

  This is why etcd backup is CRITICAL.
  If etcd is lost → entire cluster state is lost.
```

#### Scheduler — The Matchmaker

```
  When a new Pod is created (with no assigned node):
    1. Scheduler watches for unscheduled pods
    2. Filters nodes: Which nodes CAN run this pod?
       → Enough CPU/RAM available?
       → Node selector matches?
       → Taints/tolerations allow it?
    3. Scores remaining nodes: Which is BEST?
       → Least loaded?
       → Spreads pods across nodes (anti-affinity)?
    4. Assigns pod to the best node
```

#### Controller Manager — The Reconciliation Loop

```
  Controllers are the heart of Kubernetes' self-healing:

  while true:
      desired_state = read_from_api_server()
      actual_state = observe_cluster()

      if actual_state != desired_state:
          make_changes_to_match(actual_state, desired_state)

  Example: Deployment Controller
    Desired: "3 replicas of web-app"
    Actual: 2 running (1 crashed)
    Action: Start 1 more pod

  This loop runs continuously. If a pod dies → controller
  detects it and starts a replacement within seconds.
```

---

<a id="pods"></a>
## Pods — The Atom of Kubernetes

### What Is a Pod?

```
A Pod is NOT a container. A Pod is a GROUP of one or more containers
that share:
  - Network namespace (same IP, same ports)
  - Storage volumes
  - IPC (inter-process communication)
  - Lifecycle (start together, die together)

  ┌──────────────────────────────────┐
  │              POD                  │
  │  IP: 10.0.1.5                     │
  │  Volumes: [config, data]          │
  │                                   │
  │  ┌──────────┐  ┌──────────────┐  │
  │  │ Container  │  │ Container     │  │
  │  │ (web-app)  │  │ (sidecar)     │  │
  │  │            │  │ (log forward) │  │
  │  │ Port 8080  │  │                │  │
  │  └──────────┘  └──────────────┘  │
  │                                   │
  │  Both containers share:           │
  │  - localhost (can talk to each    │
  │    other via 127.0.0.1)           │
  │  - Mounted volumes                │
  └──────────────────────────────────┘
```

### Why Pods Instead of Individual Containers?

```
The Sidecar Pattern:

  Main container: Your application (Node.js web server)
  Sidecar container: Log forwarder (Fluentd)

  ┌──────────────────────────────────┐
  │  POD                              │
  │  ┌──────────┐  ┌──────────────┐  │
  │  │ Web Server │  │ Log Forwarder│  │
  │  │            │──│              │  │
  │  │ Writes logs│  │ Reads logs   │  │
  │  │ to /var/   │  │ from /var/   │  │
  │  │ log/       │  │ log/         │  │
  │  │            │  │ Sends to     │  │
  │  │            │  │ Logstash     │  │
  │  └──────────┘  └──────────────┘  │
  │         Shared Volume             │
  └──────────────────────────────────┘

  The web server doesn't need to know about log forwarding.
  The log forwarder doesn't need to know about the web app.
  But they share a volume and lifecycle.
```

### Pod Lifecycle

```
States:
  PENDING   → Pod created, waiting for scheduling or image pull
  RUNNING   → At least one container is running
  SUCCEEDED → All containers exited successfully (Job/CronJob)
  FAILED    → All containers exited, at least one with error
  UNKNOWN   → Can't reach the node (communication failure)

Restart Policy:
  Always (default):    Container dies → restart it (Deployments)
  OnFailure:           Restart only if exit code ≠ 0 (Jobs)
  Never:               Never restart (one-shot tasks)

Init Containers:
  Run BEFORE main containers, in order, must all succeed:

  ┌─ Init Container 1: "Wait for database" ─┐
  │   (checks DB is reachable)               │
  └──────────────────────────────────────────┘
                    │ (only proceeds if success)
                    ▼
  ┌─ Init Container 2: "Run migrations" ────┐
  │   (runs DB schema migration)             │
  └──────────────────────────────────────────┘
                    │
                    ▼
  ┌─ Main Container: Web App ───────────────┐
  │   (starts only after migrations done)    │
  └──────────────────────────────────────────┘
```

---

<a id="controllers"></a>
## Controllers — Deployments, StatefulSets, DaemonSets

### Deployment (Most Common)

```
A Deployment manages ReplicaSets which manage Pods.

  "I want 5 replicas of web-app:v1"

  ┌──────────────────────────────────────────────┐
  │  Deployment: web-app                          │
  │  desired: 5 replicas                          │
  │                                               │
  │  ┌──────────────────────────────────────┐    │
  │  │  ReplicaSet: web-app-abc123           │    │
  │  │  template: image: web-app:v1          │    │
  │  │                                       │    │
  │  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐    │    │
  │  │  │Pod 1│ │Pod 2│ │Pod 3│ │Pod 4│    │    │
  │  │  │     │ │     │ │     │ │     │    │    │
  │  │  └─────┘ └─────┘ └─────┘ └─────┘    │    │
  │  │  (5 pods matching template)          │    │
  │  └──────────────────────────────────────┘    │
  └──────────────────────────────────────────────┘

Rolling Update:
  kubectl set image deployment/web-app web-app=v2

  Step 1: Start 1 pod with v2 (now 6 pods: 5×v1 + 1×v2)
  Step 2: Stop 1 pod with v1 (now 5 pods: 4×v1 + 1×v2)
  Step 3: Start 1 pod with v2 (6 pods: 4×v1 + 2×v2)
  Step 4: Stop 1 pod with v1 (5 pods: 3×v1 + 2×v2)
  ... continues until all pods are v2

  Zero downtime: always enough pods running to serve traffic.
  Rollback: kubectl rollout undo deployment/web-app
```

### StatefulSet (For Stateful Apps)

```
Problem with Deployments: Pods are interchangeable (random names, random IPs).
  → Bad for databases (each node needs a STABLE identity)

StatefulSet gives pods:
  - Stable name: web-0, web-1, web-2 (NOT random suffix)
  - Stable DNS: web-0.my-service.namespace.svc.cluster.local
  - Stable storage: Each pod gets its OWN persistent volume

  Startup order (sequential):
    web-0 starts and becomes ready
    → web-1 starts and becomes ready
    → web-2 starts

  Shutdown order (reverse):
    web-2 stops
    → web-1 stops
    → web-0 stops

  Use case: Databases (PostgreSQL primary/replica), Kafka brokers,
            ZooKeeper, Elasticsearch nodes, Redis clusters.
```

### DaemonSet (One Pod Per Node)

```
  "Run this pod on EVERY node in the cluster"

  Node 1: [Pod: log-collector]
  Node 2: [Pod: log-collector]
  Node 3: [Pod: log-collector]
  Node 4: [Pod: log-collector]

  When Node 5 joins cluster → DaemonSet automatically starts a pod on it.

  Use cases:
    - Log collection (Fluentd, Filebeat)
    - Monitoring agents (Prometheus node-exporter)
    - Network plugins (Calico, Cilium)
    - Storage daemons (Ceph, GlusterFS)
```

### Job / CronJob

```
Job: Run a task to completion.
  "Run this data migration once"

  Job creates pods until 1 succeeds:
    Pod 1 fails → Pod 2 starts → Pod 2 succeeds → Job complete

CronJob: Run on a schedule.
  "Run this backup every day at 2 AM"
  schedule: "0 2 * * *"
```

---

<a id="services"></a>
## Services — How Networking Works

### The Problem

```
Pods are ephemeral:
  Pod IP: 10.0.1.5 → Pod dies → New pod gets IP: 10.0.1.9

  If your app connects to 10.0.1.5 → connection fails (pod moved)
  → Need a STABLE address that routes to whatever pod is currently running.

This is what a Service provides.
```

### Service Types

#### ClusterIP (Internal)

```
  ┌──────────────────────────────────────────┐
  │  Service: web-app-service                 │
  │  ClusterIP: 10.96.0.15 (stable, internal) │
  │  Port: 80                                  │
  │                                            │
  │  Routes to:                                │
  │  ├── Pod 1 (10.0.1.5:8080)                │
  │  ├── Pod 2 (10.0.1.6:8080)                │
  │  └── Pod 3 (10.0.2.5:8080)                │
  └──────────────────────────────────────────┘

  Other pods in the cluster can reach it:
    curl http://web-app-service:80
    → DNS resolves to 10.96.0.15
    → Load-balanced across 3 pods

  DNS: web-app-service.namespace.svc.cluster.local
```

#### NodePort (Expose on Every Node)

```
  ┌──────────────────────────────────────────┐
  │  Service: web-app-service                 │
  │  NodePort: 30080                          │
  │                                            │
  │  Accessible on ALL nodes:                  │
  │  Node1:30080 → Pod 1                       │
  │  Node2:30080 → Pod 2                       │
  │  Node3:30080 → Pod 3                       │
  └──────────────────────────────────────────┘

  External access:
    curl http://any-node-ip:30080
```

#### LoadBalancer (Cloud Provisioned)

```
  ┌──────────────────────────────────────────┐
  │  Service: web-app-service                 │
  │  LoadBalancer                              │
  │                                            │
  │  Cloud creates:                            │
  │  AWS ALB / GCP LB / Azure LB              │
  │  External IP: 54.210.1.5                  │
  │                                            │
  │  External → Cloud LB → Nodes → Pods       │
  └──────────────────────────────────────────┘

  External access:
    curl http://54.210.1.5
```

#### Ingress (HTTP Routing — Most Common for Web Apps)

```
  ┌────────────────────────────────────────────────┐
  │  Ingress Controller (Nginx/Traefik/Istio)       │
  │                                                  │
  │  Rules:                                          │
  │    api.example.com/users  → user-service:8080   │
  │    api.example.com/orders → order-service:8080  │
  │    api.example.com/pay    → pay-service:8080    │
  │                                                  │
  │  One LoadBalancer for ALL services              │
  │  (Cheaper than one LB per service)              │
  └────────────────────────────────────────────────┘

  External: https://api.example.com/users → Ingress → user-service
```

### How Service Routing Works (kube-proxy)

```
kube-proxy runs on every node and writes iptables/IPVS rules:

  Service ClusterIP: 10.96.0.15

  iptables rule:
    "If destination is 10.96.0.15:80, randomly DNAT to:
      10.0.1.5:8080 (33% probability)
      10.0.1.6:8080 (33% probability)
      10.0.2.5:8080 (34% probability)"

  → Load balancing happens at the kernel level (iptables)
  → No userspace proxy → very fast
  → Every node has the same rules → any node can route

DNS (CoreDNS):
  "web-app-service" → 10.96.0.15

  In-cluster DNS resolution:
    pod looks up "web-app-service" → CoreDNS → 10.96.0.15
    → traffic hits iptables rule → random pod selected
```

---

<a id="storage"></a>
## Storage — Volumes, PVCs, StorageClasses

### The Storage Problem

```
Pods are ephemeral. When a pod dies:
  → Its local filesystem is LOST
  → New pod starts with a fresh filesystem

For databases, this is a problem:
  → PostgreSQL writes to /var/lib/postgresql/data
  → If pod restarts → data is gone!

Solution: PersistentVolumes (PV) + PersistentVolumeClaims (PVC)
```

### How Persistent Storage Works

```
1. StorageClass (admin defines storage types):
  apiVersion: storage.k8s.io/v1
  kind: StorageClass
  metadata:
    name: fast-ssd
  provisioner: kubernetes.io/aws-ebs
  parameters:
    type: gp3
    fsType: ext4

2. PersistentVolumeClaim (user requests storage):
  apiVersion: v1
  kind: PersistentVolumeClaim
  metadata:
    name: postgres-data
  spec:
    accessModes: ["ReadWriteOnce"]
    storageClassName: fast-ssd
    resources:
      requests:
        storage: 100Gi

3. Pod uses the PVC:
  spec:
    containers:
    - name: postgres
      image: postgres:16
      volumeMounts:
      - mountPath: /var/lib/postgresql/data
        name: data
    volumes:
    - name: data
      persistentVolumeClaim:
        claimName: postgres-data

Flow:
  PVC → StorageClass → Cloud provisions EBS volume → PV created
  → Pod mounts PV → Pod writes to /var/lib/postgresql/data
  → If pod moves to another node → EBS volume detached and reattached
  → Data persists!
```

---

<a id="config"></a>
## Configuration — ConfigMaps & Secrets

### ConfigMap (Non-Sensitive Config)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  DATABASE_URL: "postgres://db:5432/myapp"
  LOG_LEVEL: "info"
  MAX_CONNECTIONS: "100"
  config.yaml: |
    server:
      port: 8080
      timeout: 30s
    features:
      dark_mode: true

# Used in a Pod:
spec:
  containers:
  - name: app
    envFrom:
    - configMapRef:
        name: app-config     # All keys become env vars
    volumeMounts:
    - mountPath: /etc/config
      name: config-volume
  volumes:
  - name: config-volume
    configMap:
      name: app-config       # Each key becomes a file in /etc/config/
```

### Secret (Sensitive Data)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-credentials
type: Opaque
data:
  username: YWRtaW4=         # base64("admin")
  password: czNjcjN0UDBzd29yZA==  # base64("s3cr3tP0sword")

# Used in a Pod:
spec:
  containers:
  - name: app
    env:
    - name: DB_USER
      valueFrom:
        secretKeyRef:
          name: db-credentials
          key: username
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: db-credentials
          key: password
```

---

<a id="autoscaling"></a>
## Auto-Scaling — HPA, VPA, Cluster Autoscaler

### HPA (Horizontal Pod Autoscaler) — Scale Pod COUNT

```
  Monitors CPU/memory usage (or custom metrics).
  Scales the number of pods up or down.

  ┌───────────────────────────────────────────────────────┐
  │  Deployment: web-app                                   │
  │  HPA: target CPU = 70%                                 │
  │                                                        │
  │  Current: 3 pods, each at 90% CPU → OVER threshold    │
  │  HPA action: Scale to 5 pods (to bring avg CPU down)  │
  │                                                        │
  │  After scaling: 5 pods, each at 54% CPU → UNDER 70%   │
  │  HPA action: No change (within target)                │
  │                                                        │
  │  Traffic drops: 5 pods, each at 20% CPU               │
  │  HPA action: Scale down to 3 pods                     │
  └───────────────────────────────────────────────────────┘

  config:
  apiVersion: autoscaling/v2
  kind: HorizontalPodAutoscaler
  spec:
    scaleTargetRef:
      apiVersion: apps/v1
      kind: Deployment
      name: web-app
    minReplicas: 3
    maxReplicas: 50
    metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Cluster Autoscaler — Scale Node COUNT

```
  HPA scales pods, but what if there's no room on existing nodes?

  All nodes at max capacity → new pods PENDING (can't schedule)
  → Cluster Autoscaler detects unschedulable pods
  → Requests new node from cloud provider
  → New node joins cluster → pending pods get scheduled

  When nodes are underutilized:
  → Cluster Autoscaler drains the node
  → Moves pods to other nodes
  → Removes the node (saves money)
```

---

<a id="scheduling"></a>
## Scheduling — How Pods Are Placed on Nodes

### Resource Requests and Limits

```yaml
spec:
  containers:
  - name: app
    resources:
      requests:          # Minimum needed (for scheduling)
        cpu: "500m"      # 0.5 CPU cores
        memory: "512Mi"
      limits:            # Maximum allowed (for enforcement)
        cpu: "1000m"     # 1 CPU core
        memory: "1Gi"

# Scheduler uses REQUESTS to find a suitable node:
#   Node has 4 cores → can fit 8 pods requesting 500m each

# kubelet uses LIMITS to enforce:
#   If container uses >1000m CPU → throttled
#   If container uses >1Gi memory → killed (OOMKilled)
```

### Node Selector (Simple)

```yaml
spec:
  nodeSelector:
    disktype: ssd      # Only schedule on nodes labeled "disktype=ssd"
```

### Affinity / Anti-Affinity (Advanced)

```yaml
# Anti-affinity: Don't put two web-app pods on the same node
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          app: web-app
      topologyKey: kubernetes.io/hostname

# This ensures pods are spread across different nodes.
# If Node 1 has a web-app pod → Node 2 gets the next one.
# High availability: one node failure only kills 1/N pods.
```

### Taints and Tolerations

```
Taint on a node: "This node is reserved for special workloads"
  kubectl taint nodes node1 dedicated=gpu:NoSchedule

  → Normal pods are REPELLED from this node
  → Only pods with a matching toleration can run here:

  tolerations:
  - key: "dedicated"
    value: "gpu"
    effect: "NoSchedule"

  Use case: GPU nodes (only ML workloads), dedicated nodes for
            specific teams, spot instances.
```

---

<a id="helm"></a>
## Helm & Operators

### Helm — Package Manager for Kubernetes

```
Without Helm:
  kubectl apply -f deployment.yaml
  kubectl apply -f service.yaml
  kubectl apply -f configmap.yaml
  kubectl apply -f secret.yaml
  kubectl apply -f ingress.yaml
  → 10+ YAML files to manage per app

With Helm:
  helm install my-app ./my-chart
  → One command installs everything
  → Values are parameterized (dev/staging/prod configs)
  → helm upgrade my-app ./my-chart (roll new version)
  → helm rollback my-app (undo last deploy)

Chart structure:
  my-chart/
  ├── Chart.yaml          (metadata)
  ├── values.yaml         (default config)
  ├── templates/
  │   ├── deployment.yaml
  │   ├── service.yaml
  │   └── ingress.yaml
  └── values-prod.yaml    (production overrides)
```

### Operators — Custom Controllers

```
An Operator is a custom Kubernetes controller that manages a specific application.

Normal K8s controllers manage pods (generic).
Operators manage entire applications (specific).

PostgreSQL Operator:
  User creates: postgres-cluster.yaml (CRD)
  → Operator reads it
  → Creates: 1 primary pod, 2 replica pods, services, PVCs
  → Configures: replication, backups, failover
  → Monitors: If primary dies → promotes replica automatically
  → Handles: version upgrades, connection pooling

Popular operators:
  - PostgreSQL Operator (CrunchyData)
  - Kafka Operator (Strimzi)
  - Redis Operator (Spotahome)
  - Elasticsearch Operator (ECK)
  - Prometheus Operator

  Operators encode human operational knowledge into software.
  "The operator does what a DBA would do."
```

---

<a id="real-apps"></a>
## How Real Companies Use K8s

| Company | Scale | Notes |
|---------|-------|-------|
| **Google** | Invented K8s (based on internal Borg) | Runs everything on K8s/Borg |
| **Spotify** | ~1,500 services on K8s | Migrated from on-prem to GKE |
| **Shopify** | Black Friday: 100K+ pods | Handles massive traffic spikes |
| **OpenAI** | Training clusters on K8s | GPU scheduling for ML |
| **Philips** | Healthcare workloads | HIPAA-compliant K8s |
| **Pinterest** | ~3,000 microservices | Migrated from EC2 to K8s |
| **Target** | Retail workloads | Holiday traffic spikes |
| **Capital One** | Banking | PCI-DSS compliant K8s |

### Shopify Black Friday Example

```
Shopify handles Black Friday traffic with K8s:

  Normal day:    ~1,000 pods per cluster
  Black Friday:  ~15,000 pods per cluster (15x spike)

  How K8s handles it:
  1. HPA detects CPU spike → scales pods from 3 to 50 per service
  2. Cluster Autoscaler detects pending pods → adds nodes
  3. New nodes join cluster → pods scheduled automatically
  4. Traffic routes through Ingress → distributes across pods

  Total time from spike to fully scaled: ~3-5 minutes
  Zero manual intervention.
```

---

<a id="build"></a>
## How YOU Can Build This

### Local Setup (minikube or kind)

```bash
# Install minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# Start a local cluster
minikube start

# Verify
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# minikube   Ready    control-plane   1m    v1.30.0
```

### Deploy Your First App

```bash
# Deploy a simple web app
kubectl create deployment hello-app --image=gcr.io/google-samples/hello-app:1.0

# Scale to 3 replicas
kubectl scale deployment hello-app --replicas=3

# Expose it
kubectl expose deployment hello-app --port=8080 --type=LoadBalancer

# Check pods
kubectl get pods
# NAME                         READY   STATUS    RESTARTS   AGE
# hello-app-5c64f5d9b6-abc12   1/1     Running   0          30s
# hello-app-5c64f5d9b6-def34   1/1     Running   0          30s
# hello-app-5c64f5d9b6-ghi56   1/1     Running   0          30s

# Auto-scale based on CPU
kubectl autoscale deployment hello-app --cpu-percent=50 --min=3 --max=10

# Update image
kubectl set image deployment/hello-app hello-app=gcr.io/google-samples/hello-app:2.0

# Watch the rolling update
kubectl rollout status deployment/hello-app

# Rollback if needed
kubectl rollout undo deployment/hello-app
```

### YAML Manifest (Production Style)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-app
  labels:
    app: web-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web-app
  template:
    metadata:
      labels:
        app: web-app
    spec:
      containers:
      - name: web-app
        image: my-app:v1.0.0
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: 250m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
        envFrom:
        - configMapRef:
            name: app-config
        - secretRef:
            name: db-credentials
---
apiVersion: v1
kind: Service
metadata:
  name: web-app-service
spec:
  selector:
    app: web-app
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-app-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web-app
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

---

## Common Interview Questions

**Q: What is a Pod and why not just run containers directly?**

A: A Pod is the smallest deployable unit in Kubernetes. It's a group of one or more containers that share the same network namespace (same IP), storage volumes, and lifecycle. We use Pods instead of individual containers because some applications need multiple tightly-coupled containers (the sidecar pattern): a web server + log forwarder, or app + Istio proxy. These containers need to share localhost and volumes, which a Pod provides.

**Q: How does Kubernetes achieve self-healing?**

A: The reconciliation loop. Controllers continuously compare the desired state (stored in etcd) with the actual state (observed in the cluster). If they differ, the controller takes action. The Deployment controller watches pods — if a pod dies, the ReplicaSet controller detects fewer replicas than desired and starts a replacement. The node controller watches nodes — if a node goes unresponsive, its pods are marked for rescheduling after a grace period (default 5 minutes). This happens automatically, typically within seconds.

**Q: Explain the difference between a Deployment and a StatefulSet.**

A: A Deployment creates interchangeable pods with random names and IPs. When a pod dies, any replacement can take its place. A StatefulSet creates pods with stable identities: web-0, web-1, web-2 (sequential naming), stable DNS names, and dedicated persistent volumes per pod. StatefulSets start pods in order (web-0 first, then web-1) and stop them in reverse. Use Deployments for stateless apps (web servers, API services). Use StatefulSets for stateful apps (databases, message queues) where each instance has a unique identity and storage.

**Q: How does service discovery work in Kubernetes?**

A: CoreDNS provides built-in DNS resolution. When a Service is created, K8s adds a DNS record: `<service-name>.<namespace>.svc.cluster.local`. Any pod in the cluster can resolve this name to the Service's ClusterIP. kube-proxy on each node writes iptables/IPVS rules that route ClusterIP traffic to individual pod IPs (load-balanced). So pod A calls `http://web-app:80` → DNS resolves to ClusterIP → iptables routes to a healthy pod. If a pod dies, the Endpoint controller updates the iptables rules automatically.

**Q: What happens when a node fails?**

A: 1) Node controller detects no heartbeat from the node (default 40s timeout). 2) Node is marked `NotReady`. 3) After `pod-eviction-timeout` (default 5 min), pods on the dead node are marked for deletion. 4) Controllers see fewer replicas than desired → schedule replacement pods on healthy nodes. 5) If using Cluster Autoscaler and all nodes are full → new node is provisioned. 6) StatefulSet pods wait for the original node's PV to be reattached (or a new PVC). Total recovery time: ~5-8 minutes.

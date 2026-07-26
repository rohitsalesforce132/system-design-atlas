# Load Balancing

## What It Is (Analogy First)

Imagine a restaurant with 1 waiter. If 100 customers walk in at once, that waiter collapses. Now imagine a host at the door who distributes customers evenly across 10 waiters. That host is a **load balancer**.

A load balancer sits in front of your servers and distributes incoming requests across multiple machines so no single server gets overwhelmed.

```
                    ┌──────────────┐
  10,000 users ────►│  Load Balancer  │
                    └──────┬───────┘
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Server 1│   │Server 2│   │Server 3│
         │(healthy)│  │(healthy)│  │(DEAD)│
         └────────┘   └────────┘   └────────┘
                           ▲
                     Load balancer detects
                     Server 3 is down and
                     stops sending traffic
```

## Why You Need It

- **Single server dies** = entire app goes down. No redundancy.
- **Single server** = max ~10,000 concurrent connections. After that, CPU/RAM saturate.
- **Load balancer** lets you add more servers horizontally as traffic grows.

## Types of Load Balancing

### 1. Layer 4 (Transport Layer) — Fast but dumb
Operates on IP + port. Doesn't look inside the request.

```
User (203.0.113.5) ──► L4 LB ──► Server 2 (10.0.0.2:80)
```
- **Pros:** Extremely fast (hardware-level). Used by HAProxy, AWS NLB.
- **Cons:** Can't make smart routing decisions (no URL-based routing).

### 2. Layer 7 (Application Layer) — Smart but slower
Looks at the actual HTTP request — URL path, headers, cookies.

```
User ──► L7 LB ──► /api/*     ──► API Servers
                ──► /images/*  ──► CDN/Static Servers
                ──► /admin/*   ──► Internal Network
```
- **Pros:** Smart routing, SSL termination, rate limiting, content-based routing.
- **Cons:** More CPU overhead. Used by Nginx, AWS ALB, HAProxy.

## Algorithms (How to Pick Which Server)

| Algorithm | How it works | Best for |
|-----------|-------------|----------|
| **Round Robin** | Server 1 → 2 → 3 → 1 → 2 → 3 | Equal-capacity servers |
| **Weighted Round Robin** | Server 1 (powerful) gets 5 reqs, Server 2 (weak) gets 2 | Mixed hardware |
| **Least Connections** | Send to server with fewest active connections | Long-lived connections (chat, streaming) |
| **IP Hash** | Same user IP always goes to same server | Sticky sessions without cookies |
| **Random** | Pick randomly | Quick setup, simple |
| **Consistent Hashing** | Same key always maps to same server | Caching layers (Redis clusters) |

## Health Checks

The load balancer continuously pings your servers:

```
Every 5 seconds:
  LB ──HTTP GET /health──► Server 1 ──► 200 OK ✓ (keep sending traffic)
  LB ──HTTP GET /health──► Server 2 ──► 200 OK ✓ (keep sending traffic)
  LB ──HTTP GET /health──► Server 3 ──► TIMEOUT ✗ (STOP sending traffic, alert ops)
```

If a server fails 3 consecutive health checks → removed from pool.
If it recovers → added back automatically.

## DNS-Based Load Balancing (Global Scale)

At global scale, you use DNS to point users to the nearest data center:

```
User in India asks DNS: "What's the IP of netflix.com?"
DNS responds: "Use 52.x.x.x (Mumbai data center)"

User in USA asks DNS: "What's the IP of netflix.com?"
DNS responds: "Use 54.x.x.x (Virginia data center)"
```

**Techniques:**
- **GeoDNS:** Route based on user's geographic location.
- **Anycast:** Same IP announced from multiple data centers; BGP routes to nearest.
- **Latency-based routing:** Route to data center with lowest ping.
- **Weighted routing:** Gradually shift traffic (e.g., 90% old version, 10% new version for canary deploy).

## Active-Passive vs Active-Active

### Active-Passive (Failover)
```
User ──► [Active LB] ──► Primary DC (100% traffic)
                │
                └──► Backup DC (0% traffic, standing by)

If Primary DC dies → failover to Backup DC
```
- **Pros:** Simple, cost-effective.
- **Cons:** Backup sits idle (wasted money). Failover takes minutes.

### Active-Active
```
User ──► Global DNS ──► Mumbai DC (40% traffic)
                    ──► Virginia DC (35% traffic)
                    ──► Dublin DC (25% traffic)
```
- **Pros:** All servers utilized. If one DC dies, others absorb load.
- **Cons:** Complex (data synchronization across regions).

## Real-World Examples

| Company | Load Balancer Stack |
|---------|-------------------|
| **Netflix** | AWS ALB + Zuul (custom L7 gateway) |
| **Google** | Maglev (custom software L4 LB) + Google Front End (GFE) |
| **Facebook** | Proxygen (custom L7), Katran (L4 on top of XDP) |
| **Uber** | AWS ALB + Envoy proxy for service-to-service |
| **WhatsApp** | Custom Erlang distribution (no traditional LB — Erlang's built-in clustering) |

## How YOU Can Build This

### Level 1: Single Server (No LB)
```
Browser ──► Nginx ──► Node.js app (single instance)
```

### Level 2: Add a Load Balancer
```
Browser ──► Nginx (as LB) ──► Node.js instance 1
                           ──► Node.js instance 2
                           ──► Node.js instance 3
```

**Nginx config:**
```nginx
upstream my_app {
    server 10.0.0.1:3000;
    server 10.0.0.2:3000;
    server 10.0.0.3:3000;
}

server {
    listen 80;
    location / {
        proxy_pass http://my_app;
    }
    location /health {
        return 200 "OK";
    }
}
```

### Level 3: Cloud Load Balancer
- Use AWS ALB + Auto Scaling Group
- Traffic spikes → AWS automatically adds more servers
- Traffic drops → AWS removes servers (save money)

## Common Interview Questions

**Q: What happens if the load balancer itself dies?**
A: This is a single point of failure. Solutions:
1. Run LB in active-passive pair (keepalived + VRRP).
2. Use cloud-managed LBs (AWS ALB is inherently HA).
3. Use multiple DNS entries (if one LB IP dies, client retries the next).

**Q: Round Robin vs Least Connections — when to use which?**
A: Round Robin assumes all requests take the same time. If some requests are heavy (video upload) and others light (API call), use Least Connections so heavy requests don't pile up on one server.

**Q: How do you handle sticky sessions?**
A: Three ways:
1. **Cookie-based:** LB sets a cookie, same server handles that user.
2. **IP Hash:** Same source IP → same server. Problem: users behind NAT share IPs.
3. **Session store:** Don't make sessions sticky. Put sessions in Redis so any server can serve any user. ← **Best practice.**

## Key Trade-offs Summary

| Decision | Option A | Option B | When to pick |
|-----------|---------|---------|-------------|
| Layer | L4 | L7 | L4 for raw speed, L7 for smart routing |
| Stickiness | Sticky sessions | Shared session store | Shared store is more scalable |
| Topology | Active-Passive | Active-Active | Active-Active for max utilization |
| Health checks | TCP check | HTTP /health | HTTP check knows if app is truly healthy |

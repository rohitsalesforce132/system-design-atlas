# Google Maps — System Design Atlas

> **One-line summary:** Google Maps is a planetary-scale geographic information system (GIS) that
> renders the entire Earth as a pyramid of image tiles, computes driving/walking/transit routes over
> a graph of hundreds of millions of road segments, and layers live traffic and places data on top —
> serving over a billion users.

---

## 1. Overview & Scale Numbers

Google Maps looks simple: a slippy map you pan and zoom. Underneath it is one of the largest data
systems ever built. Three distinct hard problems are fused together:

1. **Rendering** the map — serving pre-rendered image tiles at the right zoom level, fast.
2. **Routing** — finding the best path over a global road graph in milliseconds.
3. **Places & Search** — finding "coffee near me" among hundreds of millions of POIs (Points of
   Interest).

Each is a serious system-design problem on its own.

### The numbers

| Metric                                      | Approximate value          | Why it matters                                          |
| ------------------------------------------- | -------------------------- | ------------------------------------------------------- |
| Monthly active users                        | ~1B+                       | Truly global, every country                             |
| Countries mapped                            | 200+                       | Multi-language, multi-script                            |
| Road segments in routing graph              | hundreds of millions       | Drives the graph size and partitioning strategy          |
| Points of Interest (POIs)                   | 250M+                      | Places search + business listings                       |
| Imagery (Street View + satellite)           | petabytes, 150B+ images    | Stored on disk + served via tiles                       |
| Imagery refresh / km driven                 | continuous capture fleets  | Freshness matters for construction/roads                |
| Tile pyramid levels (zoom)                  | 0–22                       | Level 0 = whole world in 1 tile; 22 = street-level       |
| Tile count at max zoom                      | quadrillions               | Most never requested; served on demand                  |
| Live traffic update frequency               | seconds-to-minutes         | ETAs must reflect real conditions                       |
| Route computation latency target            | <1 second                  | Users won't wait                                        |

### The product goal

You open Maps, type "pizza near me", see results ranked by distance/relevance, pick one, tap
"Directions", choose driving, and within a second see a route with ETA and traffic. You start
driving; the map follows you, re-routes if you miss a turn, and warns you about delays ahead. All
of this must work on a phone with intermittent connectivity.

---

## 2. High-Level Architecture

Google Maps decomposes into **four data planes** that meet at the client:

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                          USER DEVICE                                │
   │         browser / Android / iOS app (renders tiles + UI)           │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │  HTTPS
                                  ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                   FRONTEND / API LAYER                              │
   │       (load balancing, auth, rate limit, request routing)           │
   └──────┬───────────────┬──────────────────┬──────────────────┬───────┘
          │               │                  │                  │
          ▼               ▼                  ▼                  ▼
   ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  MAP TILES │  │   ROUTING    │  │   PLACES     │  │   TRAFFIC    │
   │   SERVICE  │  │   SERVICE    │  │   SEARCH     │  │   SERVICE    │
   │            │  │ (graph algo) │  │   SERVICE    │  │ (live data)  │
   └─────┬──────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                │                 │                 │
         ▼                ▼                 ▼                 ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                         DATA LAYER                                │
   │  Tile store (imagery)  •  Road graph DB  •  POI index             │
   │  Traffic store (time-series)  •  Geocoding index                 │
   │  (Bigtable / Spanner / custom spatial indexes)                   │
   └──────────────────────────────────────────────────────────────────┘
```

### The four planes

- **Tiles plane:** the visual map. Pre-rendered images served from CDN.
- **Routing plane:** the road graph + pathfinding algorithm.
- **Places plane:** POIs and business data, searched spatially + textually.
- **Traffic plane:** real-time speeds per road segment, updated continuously.

---

## 3. Detailed Component Breakdown

### 3.1 The Map Tiles Service (rendering the world)

The world is too big to render at once. The solution is the **tile pyramid**: at zoom level 0 the
whole Earth is one 256×256 image. Each zoom level doubles the resolution, quartering each tile
into 4 children. By level 22 you have street-level detail.

```
   Zoom 0                          Zoom 1
   ┌───────────────┐               ┌───────┬───────┐
   │               │               │  0/0  │  1/0  │
   │  whole Earth  │    ──────▶    ├───────┼───────┤
   │   256x256     │               │  0/1  │  1/1  │
   │               │               └───────┴───────┘
   └───────────────┘
```

A tile is addressed by `(z, x, y)` — zoom, column, row. The client computes which tiles are visible
given the viewport and zoom, and fetches exactly those.

```
   client viewport
   ┌──────────────────────────────┐
   │   ┌────┬────┬────┐           │
   │   │ z14│ z14│ z14│  visible  │   GET /tile/14/8521/5820.png
   │   ├────┼────┼────┤  tiles    │   GET /tile/14/8522/5820.png
   │   │ z14│ z14│ z14│           │   ... (6-12 tiles typically)
   │   └────┴────┴────┘           │
   └──────────────────────────────┘
```

Tiles are **pre-rendered** from vector data (roads, parks, water, buildings) for the common zoom
levels and cached aggressively in CDN. For less-traveled areas, tiles may be rendered on demand
from vector data and cached after first request.

The vector data itself comes from Google's base map — a constantly-updated database of roads,
boundaries, and features sourced from satellite imagery, Street View, and third-party data.

### 3.2 The Routing Service (pathfinding at planetary scale)

Routing is a **graph problem**. Nodes are intersections; edges are road segments with a cost
(typically travel time). The classic algorithm is **Dijkstra** (find shortest path) or its
optimized variant **A\*** (use a heuristic to guide the search toward the destination).

The challenge: the graph has hundreds of millions of edges. A naive Dijkstra from coast to coast
would explore too much.

**The key optimization: contraction hierarchies (CH).** The graph is preprocessed offline: less
"important" nodes are contracted, leaving a hierarchy of increasingly important roads. At query
time, the search alternately goes "up" the hierarchy from source and destination until they meet
at a highway. This makes continent-scale routing take milliseconds.

```
   source                              destination
     │                                      │
     │  go up the hierarchy                 │  go up the hierarchy
     ▼                                      ▼
   local ──▶ arterial ──▶ highway ══════════ highway ◀── arterial ◀── local
                                   │
                          (meet here, on a highway)
```

Other techniques: **bidirectional search** (search from both ends simultaneously), **A\* with
landmarks** (ALT algorithm), and **partitioning the graph** by region so a query only touches
relevant partitions.

The routing graph is stored in a specialized in-memory structure (custom or based on something
like OSRM's approach) for sub-second queries. Edge weights (travel times) are updated from live
traffic data.

### 3.3 The Traffic Service

Live traffic is what makes Maps' ETAs trustworthy. Two sources:

1. **Anonymous phone GPS** from Android/iPhone users driving on roads (with consent).
2. **Historical patterns** per road segment, per time-of-day, per day-of-week.

```
   phones driving on I-280
        │ GPS pings
        ▼
   ┌──────────────────────────┐
   │   TRAFFIC PIPELINE       │
   │                          │
   │  1. snap pings to roads  │   (map matching)
   │  2. compute speed/seg    │
   │  3. combine w/ history   │
   │  4. store + publish      │
   └─────────────┬────────────┘
                 ▼
   routing graph edge weights updated
   ETA recomputed
   traffic overlay drawn on map (red/yellow/green)
```

The **map matching** step is non-trivial: a raw GPS point might be 20m off; you must snap it to
the most likely road segment, accounting for direction and previous positions (a Hidden Markov
Model is commonly used).

### 3.4 The Places / Search Service

Places search answers "coffee near me" or "Empire State Building". It combines:

- **Text search** over POI names/descriptions (inverted index).
- **Spatial filtering** — only POIs within a radius/bounding box.
- **Ranking** by relevance, distance, rating, popularity.

```
   query: "coffee", location: (40.74, -73.98), radius: 1km
        │
        ▼
   ┌──────────────────────────────────────────┐
   │  1. text search: POIs named "coffee"     │
   │  2. spatial filter: within 1km of point  │
   │  3. rank by: distance, rating, popularity│
   │  4. return top N with locations          │
   └──────────────────────────────────────────┘
```

Underlying tech: a spatially-aware search index. Approaches include **geohash prefix matching** in
Elasticsearch, **R-trees** for bounding-box queries, or **Google's internal spatial index** with
both text and geo predicates.

### 3.5 Geocoding

Translates addresses ↔ coordinates. "1600 Amphitheatre Pkwy" → `(37.42, -122.08)` and vice versa
(reverse geocoding). Backed by a massive lookup table + ML models for ambiguous addresses.

### 3.6 Street View

A separate imagery pipeline: 360° cameras mounted on cars/backpacks capture panoramas. Each pano
is geolocated, stitched, and served on demand when a user drops the Pegman. Stored as specialized
image pyramids, separate from the base map tiles.

---

## 4. Data Model

### 4.1 Core entities

```
   ┌──────────────────┐         ┌──────────────────┐
   │   Road Segment   │         │   Intersection   │
   │ - id             │◀───────▶│ - id             │
   │ - from_node      │         │ - lat, lng       │
   │ - to_node        │         │ - type           │
   │ - length_m       │         └──────────────────┘
   │ - speed_limit    │
   │ - road_class     │     ┌──────────────────┐
   │ - one_way?       │     │   POI / Place    │
   │ - live_speed     │     │ - id             │
   │   (time-varying) │     │ - name           │
   └──────────────────┘     │ - lat, lng       │
                            │ - category       │
   ┌──────────────────┐     │ - rating         │
   │   Tile           │     │ - hours          │
   │ - z, x, y        │     └──────────────────┘
   │ - image (blob)   │
   │ - vector_data?   │     ┌──────────────────┐
   │ - render_date    │     │   Traffic Sample │
   └──────────────────┘     │ - segment_id     │
                            │ - speed          │
                            │ - timestamp      │
                            └──────────────────┘
```

### 4.2 Storage choices

| Data                            | Store                          | Why                                  |
| ------------------------------- | ------------------------------ | ------------------------------------ |
| Pre-rendered tiles              | Custom blob store + CDN        | Read-heavy, cacheable, immutable     |
| Base map vector data            | Spanner / custom spatial DB    | Global, strongly consistent edits    |
| Routing graph (in-memory)       | Custom in-memory structure     | Sub-second pathfinding               |
| POIs                            | Spatial index + KV store       | Text + geo queries                   |
| Live traffic                    | Bigtable (time-series)         | High write rate, sparse, time-keyed  |
| Imagery (satellite/Street View) | Colossus (GFS successor) + CDN | Petabytes, served as tiles           |
| Geocoding index                 | Bigtable + in-memory           | Fast lookup, ML for ambiguity        |

### 4.3 Why Bigtable for traffic

Traffic data is a **time-series**: for each road segment, speed samples arrive continuously.
Bigtable (like Apache HBase / Cassandra) is perfect: rows keyed by `segment_id#timestamp`,
append-only writes, sparse columns, horizontal scaling. Reads are "give me the latest speed for
these N segments".

### 4.4 Why the routing graph is in-memory

Pathfinding touches many edges with random access. Disk latency would kill query time. The graph
is loaded into RAM on routing servers, partitioned by region, with replication for throughput.

---

## 5. Request Flow — Navigating a Route

Let's trace the most important user action: **searching for a destination and getting driving
directions**.

```
USER DEVICE      FRONTEND     PLACES SVC    GEOCODE    ROUTING SVC    TRAFFIC SVC    TILES CDN
   │                │             │            │            │              │             │
   │─type "pizza"──▶│             │            │            │              │             │
   │ near me        │─search──────▶│            │            │              │             │
   │                │             │─text + spatial query                    │             │
   │                │◀─POI list────┤            │            │              │             │
   │◀─results───────┤             │            │            │              │             │
   │                │                                                          │
   │─tap "Directions▶                                                         │
   │  + current loc │                                                          │
   │                │                                                          │
   │                │  (meanwhile, map view is fetching tiles for the area)   │
   │──────────────────────── tiles request ──────────────────────────────────▶│
   │◀──────────────── image tiles ────────────────────────────────────────────│
   │                │             │            │            │              │             │
   │─tap "Start"────▶│──────────────────────────────────────▶│              │             │
   │                │             │            │  compute route:            │             │
   │                │             │            │  1. snap endpoints to graph│             │
   │                │             │            │  2. contraction hierarchies│             │
   │                │             │            │  3. fetch live edge weights◀─────────────│
   │                │             │            │◀──speed per segment────────┤             │
   │                │             │            │  4. pick best route + ETA  │             │
   │                │             │            │◀─route polyline + steps────┤             │
   │                │◀─directions──────────────────────────────┤             │             │
   │◀─route drawn───┤                                                          │
   │   on map       │                                                          │
   │                │                                                          │
   │   driving...                                                              │
   │   GPS pings sent periodically                                            │
   │                │             │            │            │              │             │
   │   (if user deviates from route)                                          │
   │                │──────────────────────────────────────▶│ (re-route)   │             │
   │                │◀─new route─────────────────────────────┤              │             │
   │◀─recalculating─┤                                                          │
   │                │                                                          │
   │   arrive                                                                 │
```

**Step-by-step:**

1. **User searches.** "Pizza near me" → Places service. Query includes location + radius.
2. **Places service** does a text + spatial search, returns ranked POIs with coordinates,
   ratings, hours.
3. **Map renders.** Client computes visible tiles for current viewport + zoom, fetches them from
   CDN. Tiles arrive as PNG/WebP images; client composites them in a canvas.
4. **User taps Directions.** Client sends origin (current GPS) + destination (POI coords) +
   travel mode (driving).
5. **Routing service computes the route:**
   - **Snap** origin and destination to the nearest nodes on the road graph (the GPS point may
     not be exactly on a road).
   - **Run contraction hierarchies** (or A\*) to find the shortest-time path.
   - **Fetch live edge weights** — current speeds per segment from the Traffic service.
   - **Rank alternative routes** if multiple exist (fastest vs. shortest vs. fuel-efficient).
   - **Compute ETA** by summing edge travel times.
6. **Route returned** as a polyline (encoded lat/lng sequence) + step-by-step instructions +
   ETA.
7. **Client draws the route** on the map and begins turn-by-turn navigation.
8. **While driving**, the client sends GPS pings periodically. The server may track the user's
   position against the route.
9. **If the user deviates**, the client detects it (or the server does) and triggers a re-route:
   new origin = current GPS, same destination, new computation.
10. **Live traffic updates** can also trigger re-routing mid-trip if a faster path appears.

---

## 6. Scaling Strategy

### 6.1 Tile pyramid + CDN

Tiles are the most-requested artifact (every pan/zoom fetches several). They are:
- **Pre-rendered** for common zooms.
- **Served from CDN edge nodes** close to users.
- **Addressed by (z,x,y)** — a deterministic URL, perfectly cacheable.

This is why panning the map feels instant: tiles come from a nearby CDN cache, not from Google's
origin.

### 6.2 Graph partitioning for routing

The global road graph is too big for one machine. It's partitioned by region (with overlap at
boundaries). A routing query touches the source region, destination region, and the "highway"
hierarchy that connects them. Routing servers are replicated per region for throughput.

### 6.3 Contraction hierarchies preprocessing

The CH preprocessing is expensive (hours) but done **offline**. The result is a compact
query-optimized graph that fits in memory and answers in milliseconds. When the road graph
changes (new road, closure), preprocessing is re-run incrementally.

### 6.4 Time-series scaling for traffic

Bigtable handles the traffic write rate (millions of GPS samples/sec aggregated into per-segment
speeds). Reads are "latest speed for segment X" — a fast point lookup.

### 6.5 Vector tiles + client-side rendering (modern Maps)

Modern Google Maps increasingly uses **vector tiles** instead of image tiles. The client receives
vector data (roads, labels, buildings as geometry) and renders with WebGL. Benefits:
- Smaller payload (vectors compress better than images).
- Smooth zoom/rotate/3D without re-fetching.
- Dynamic styling (night mode, highlighting route).

The trade-off: more client CPU/GPU required, and more complex client code.

### 6.6 Offline maps

Clients can cache tiles + a routing graph subset for offline use. This requires compact storage
on-device (e.g., OSM's PBF format or custom).

---

## 7. Tech Stack

| Layer                       | Technology                                            |
| --------------------------- | ----------------------------------------------------- |
| Cloud                       | Google infrastructure (Borg/Kubernetes, Colossus, Bigtable) |
| Frontend                    | Android (Kotlin/Java), iOS (Objective-C/Swift), Web (JS/WebGL) |
| Map rendering               | Custom raster tile pipeline + vector tiles (WebGL)    |
| Routing                     | Custom graph engine (contraction hierarchies, A*)      |
| Databases                   | Spanner (global consistent), Bigtable (time-series), KV stores |
| Search                      | Internal inverted index + spatial index               |
| Geocoding                   | ML models + lookup tables                             |
| Imagery                     | Colossus (GFS successor), custom image pyramids        |
| ML / map matching           | TensorFlow / internal ML                              |
| Data ingestion              | Flume (internal MapReduce), Beam/Apache Flume          |
| Languages                   | C++, Java, Python, Go                                  |
| Edge                        | Google Front End (GFE) + global CDN                    |

---

## 8. How YOU Can Build a Simplified Version

### 8.1 Minimal architecture

```
   ┌────────────┐    /tile/z/x/y     ┌──────────────┐    ┌──────────────┐
   │  Browser   │◀──────────────────▶│  tile server │◀──▶│  tile cache  │
   │ (Leaflet)  │                    │  (Node/Flask)│    │  (Redis/files)│
   │            │                    └──────────────┘    └──────────────┘
   │            │
   │            │    /route?from&to   ┌──────────────┐    ┌──────────────┐
   │            │◀──────────────────▶│  routing svc │◀──▶│  OSRM or     │
   │            │                    │              │    │  GraphHopper │
   │            │                    └──────────────┘    └──────────────┘
   │            │
   │            │    /search?q        ┌──────────────┐    ┌──────────────┐
   │            │◀──────────────────▶│ places svc   │◀──▶│  POI db      │
   │            │                    └──────────────┘    │  (Postgres + │
   │            │                                        │   PostGIS)   │
   │            │                                        └──────────────┘
```

### 8.2 Step-by-step build

1. **Map tiles.** Use **OpenStreetMap** raster tiles (free) or self-host with `tilemaker` /
   `renderd`. Point Leaflet at them:
   ```html
   <div id="map" style="height:600px"></div>
   <script>
     const map = L.map('map').setView([40.74, -73.98], 13);
     L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
   </script>
   ```
2. **Routing.** Run **OSRM** or **GraphHopper** locally with an OSM extract (e.g.,
   `geofabrik.de` for a city/region). Both expose an HTTP API:
   ```
   GET http://localhost:5000/route/v1/driving/{lng1},{lat1};{lng2},{lat2}
   ```
   Returns a polyline, steps, and ETA.
3. **Draw the route** with Leaflet:
   ```javascript
   const route = L.polyline(decodedCoords, {color:'blue'}).addTo(map);
   map.fitBounds(route.getBounds());
   ```
4. **Places search.** Load POIs into Postgres with the **PostGIS** extension:
   ```sql
   CREATE TABLE pois (
     id SERIAL, name TEXT, category TEXT,
     geom GEOMETRY(Point, 4326)
   );
   CREATE INDEX ON pois USING GIST(geom);
   ```
   Query "coffee within 1km":
   ```sql
   SELECT name, ST_Distance(geom, ST_MakePoint(-73.98, 40.74)::geography) AS dist
   FROM pois
   WHERE category = 'coffee'
     AND ST_DWithin(geom, ST_MakePoint(-73.98, 40.74)::geography, 1000)
   ORDER BY dist LIMIT 20;
   ```
5. **Geocoding.** Use the free **Nominatim** API (OSM's geocoder) or self-host it.
6. **Live traffic (simplified).** You won't have phone fleets, but you can use historical average
   speeds per road class (OSM tags `highway=motorway` etc.) and feed them as edge weights to
   OSRM/GraphHopper.
7. **Turn-by-turn navigation.** Use the browser's `navigator.geolocation` to track the user,
   compare position to the route polyline, and trigger a re-route on deviation.

### 8.3 What you'll learn

- How tile pyramids make a planet-sized map serveable.
- Why routing is a graph problem and how contraction hierarchies make it fast.
- How spatial indexes (PostGIS GIST, geohash) make "near me" queries fast.
- Why live traffic is a time-series + map-matching problem.

### 8.4 Cost for a weekend build

- OSM tiles: free (with usage limits; for production use a paid provider).
- OSRM/GraphHopper: free, self-hosted.
- Postgres + PostGIS: free.
- A $5 VPS handles a single city easily. Scaling to the planet is where Google's billions go.

---

## 9. Key Design Decisions & Trade-offs

| Decision                                        | Alternative considered        | Why Google chose it                                    |
| ----------------------------------------------- | ----------------------------- | ------------------------------------------------------ |
| **Tile pyramid + CDN for rendering**            | Render on demand server-side  | Cacheable, fast, offloads origin                       |
| **Contraction hierarchies for routing**         | Plain Dijkstra                | Continent-scale routing in milliseconds                |
| **In-memory routing graph**                     | Disk-backed graph             | Sub-second queries require RAM speed                   |
| **Vector tiles (modern)**                       | Raster tiles only             | Smaller payloads, smooth zoom/3D, dynamic styling      |
| **Bigtable for traffic time-series**            | Relational DB                 | Write rate + sparse time-keyed data                    |
| **Map matching via HMM**                        | Snap to nearest road          | Handles GPS noise, overpasses, parallel roads          |
| **Phone GPS for traffic**                       | Road sensors only             | Global coverage without installing hardware            |
| **Pre-rendered + on-demand hybrid**             | All on-demand                 | Popular tiles pre-rendered; rare tiles on demand       |

### The deepest trade-off

**Freshness vs. cost for the base map.** The world changes constantly (new roads, construction).
Google could re-image everything daily, but that's prohibitively expensive. Instead they use a
**tiered freshness model**: high-traffic areas and user-reported changes are updated frequently;
remote areas are refreshed rarely. Live traffic covers the "right now" layer; the base map covers
"recently". Users accept slightly stale imagery in exchange for a free, global map.

---

## 10. Common Interview Questions

**Q1: How would you design Google Maps?**
Split into four planes: tiles (rendering), routing (graph), places (search), traffic (live data).
Explain the tile pyramid and CDN for rendering, contraction hierarchies for routing, spatial
index for places, and time-series for traffic.

**Q2: How do you render the map at different zoom levels?**
Tile pyramid: zoom 0 = 1 tile for the world, each level doubles resolution. Client fetches visible
`(z,x,y)` tiles from CDN. Pre-rendered for common zooms; on-demand for rare ones.

**Q3: How do you find the shortest driving route quickly?**
Model roads as a graph. Use Dijkstra/A* for small areas. For continent-scale, preprocess with
contraction hierarchies so queries only traverse the "important road" hierarchy and meet at a
highway. Result: millisecond queries.

**Q4: How does live traffic work?**
Anonymous phone GPS → snap to road segments (map matching) → compute speed per segment → store as
time-series → update routing edge weights → recompute ETAs and traffic overlay.

**Q5: How do you handle "coffee near me"?**
Spatial search: filter POIs by text ("coffee") and by bounding box/radius (PostGIS GIST index or
geohash). Rank by distance, rating, relevance.

**Q6: How do you keep the map fresh when roads change?**
Tiered freshness. User reports + satellite re-imagery update the base map. Changes propagate to
tiles (re-render) and the routing graph (re-preprocess CH). Live traffic handles "right now".

**Q7: Why vector tiles instead of image tiles?**
Smaller payloads, smooth zoom/rotate/3D, dynamic styling (night mode), client-side label
collision. Trade-off: more client compute.

**Q8: How do you scale routing globally?**
Partition the graph by region (with boundary overlap). Replicate routing servers per region. CH
preprocessing keeps queries fast. Live traffic edge weights are read from a fast lookup store.

**Q9: What if GPS is inaccurate?**
Map matching using a Hidden Markov Model: considers the sequence of GPS points, road network
topology, and transition probabilities to find the most likely actual path, not just the nearest
road to each ping.

**Q10: How is ETA computed?**
Sum of edge travel times along the chosen route. Each edge's travel time = function of distance,
speed limit, road class, and live/historical traffic. Live traffic gives "now"; historical gives
"predicted at 8am Tuesday".

---

## Further reading

- Google Maps Platform blog & docs.
- "Contraction Hierarchies: Faster and Simpler Hierarchical Routing in Road Networks"
  (Geisberger et al., 2008).
- OpenStreetMap wiki on tile rendering, OSRM, GraphHopper.
- "Hidden Markov Map Matching Through Noise and Sparseness" (Newson & Krumm, 2009) — the classic
  map-matching paper.
- Google's "Behind the Scenes" blog posts on Maps.

---

*Last updated: July 2026. Numbers approximate, based on public disclosures and engineering talks.*

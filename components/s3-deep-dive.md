# Amazon S3 — The Complete Deep Dive

> S3 is mentioned 147 times across this atlas. Netflix stores every movie, Airbnb every photo, Amazon every product image on S3. This guide covers how S3 achieves 99.999999999% durability, how storage classes save money, and how to serve billions of objects.

---

## Table of Contents

1. [What Problem S3 Solves](#the-problem)
2. [Architecture — How S3 Stores Objects Internally](#architecture)
3. [Data Model — Buckets, Keys, Objects](#data-model)
4. [Durability — How 11 Nines Works](#durability)
5. [Storage Classes — When to Use Each](#storage-classes)
6. [Consistency Model](#consistency)
7. [Versioning](#versioning)
8. [Lifecycle Policies](#lifecycle)
9. [Performance — Prefixes, Multipart Upload, Range Gets](#performance)
10. [Security](#security)
11. [Event Notifications](#events)
12. [How Real Companies Use S3](#real-apps)
13. [How YOU Can Build This](#build)

---

<a id="the-problem"></a>
## What Problem S3 Solves

### The File Storage Problem

```
Storing user-generated content (photos, videos, documents):

TRADITIONAL (filesystem on a server):
  Server disk: /var/uploads/user_1001_photo.jpg

  Problems:
  1. Disk fills up → need to add more disks
  2. Server crashes → files are lost (unless RAID)
  3. Can't serve millions of concurrent downloads
  4. No redundancy across data centers
  5. Backup is manual and error-prone
  6. Scaling = buying bigger servers (vertical)

S3 (object storage):
  Upload to S3 → stored across multiple facilities → 
  served globally via CDN → infinite capacity →
  99.999999999% durability → zero maintenance

  S3 is not a filesystem. It's a flat key-value store for files.
  Key = filename. Value = file content. Metadata = content-type, etc.
```

### S3 vs Filesystem vs Database

```
┌──────────────┬─────────────┬──────────────┬──────────────┐
│ Feature      │ Filesystem   │ Database      │ S3            │
├──────────────┼─────────────┼──────────────┼──────────────┤
│ Store files  │ ✓ (native)  │ ✗ (BLOB)     │ ✓ (native)   │
│ Read/write   │ Block I/O   │ Row-based     │ HTTP API     │
│ Scale        │ One server   │ Shardable    │ Infinite     │
│ Durability   │ RAID (99.9%)│ Replication  │ 99.999999999%│
│ Max file     │ FS limit     │ ~1GB (BLOB)  │ 5 TB         │
│ Query        │ No           │ SQL          │ S3 Select    │
│ Latency      │ ~0.1ms       │ ~1-10ms      │ ~20-100ms    │
│ Cost/GB/mo   │ ~$0.10       │ ~$0.30       │ $0.023       │
│ Best for     │ OS files     │ Structured   │ Objects/files│
└──────────────┴─────────────┴──────────────┴──────────────┘
```

---

<a id="architecture"></a>
## Architecture — How S3 Stores Objects Internally

### The Key-Value Store Model

```
S3 is essentially a massive distributed key-value store:

  ┌───────────────────────────────────────────────────────┐
  │  S3 INTERNAL ARCHITECTURE                              │
  │                                                       │
  │  ┌─────────────┐                                      │
  │  │  Request     │  PUT /bucket/key → store object     │
  │  │  Router      │  GET /bucket/key → retrieve object  │
  │  └──────┬──────┘                                      │
  │         │                                              │
  │  ┌──────▼──────┐                                      │
  │  │  Index       │  Maps key → storage location         │
  │  │  Service     │  (which disks, which AZs hold it)    │
  │  └──────┬──────┘                                      │
  │         │                                              │
  │  ┌──────▼──────────────────────────────────────────┐ │
  │  │  STORAGE NODES (across 3+ Availability Zones)    │ │
  │  │                                                   │ │
  │  │  AZ-1              AZ-2              AZ-3         │ │
  │  │  ┌──────┐         ┌──────┐         ┌──────┐     │ │
  │  │  │Disk A│         │Disk D│         │Disk G│     │ │
  │  │  │Copy 1│         │Copy 2│         │Copy 3│     │ │
  │  │  └──────┘         └──────┘         └──────┘     │ │
  │  └───────────────────────────────────────────────────┘ │
  └───────────────────────────────────────────────────────┘

  When you PUT an object:
    1. Request router receives the object
    2. Index service assigns a storage location
    3. Object is written to disk in 3 AZs (6+ copies total)
    4. All copies confirmed → success response
```

### How S3 Differs from a Filesystem

```
FILESYSTEM (tree structure):
  /
  ├── users/
  │   ├── 1001/
  │   │   ├── photo.jpg
  │   │   └── resume.pdf
  │   └── 1002/
  │       └── photo.jpg
  └── products/
      └── images/
          └── shoe_red.jpg

  → Real directories, real hierarchy
  → Renaming a directory moves all files

S3 (FLAT key-value):
  Bucket: my-app-assets

  Key: users/1001/photo.jpg      ← This is the ENTIRE key
  Key: users/1001/resume.pdf
  Key: users/1002/photo.jpg
  Key: products/images/shoe_red.jpg

  → There are NO real folders. "users/1001/" is just a prefix.
  → The ENTIRE string "users/1001/photo.jpg" is the key.
  → "Folders" are just visual grouping in the AWS console.
  → You can have unlimited "depth" but it's all flat keys.
```

---

<a id="durability"></a>
## Durability — How 11 Nines Works

### The Math of 99.999999999% Durability

```
  11 nines = 99.999999999%

  What this means:
  → If you store 10,000,000 objects
  → You can expect to lose 1 object every 10,000 years

  How S3 achieves this:

  1. REDUNDANCY (3 copies across 3 AZs):
     AZ-1: Copy 1 + Copy 2
     AZ-2: Copy 3 + Copy 4
     AZ-3: Copy 5 + Copy 6
     → 6 copies on different disks in different data centers

  2. CHECKSUMS:
     → Every object has checksums stored with it
     → On every read: verify checksum
     → If checksum fails → fetch from another copy

  3. BACKGROUND SCRUBBING:
     → S3 continuously scans stored objects
     → Detects bit rot (silent data corruption)
     → Reconstructs corrupted copies from healthy ones

  4. ERASURE CODING (S3 uses for Glacier):
     → Instead of full copies: store data + parity
     → Like RAID 6: can lose 2 disks and still recover
     → Less storage overhead than 3x replication

  5. ANTI-ENTROPY:
     → Periodic comparison of replicas
     → If any copy differs → repair from majority
```

### Durability vs Availability

```
  DURABILITY: Will your data still exist in 10 years?
    → S3 Standard: 99.999999999% (11 nines)
    → S3 Glacier: 99.999999999% (11 nines)
    → Almost certain your data will never be lost.

  AVAILABILITY: Can you access your data RIGHT NOW?
    → S3 Standard: 99.99% (4 nines = ~52 min downtime/year)
    → S3 Glacier: retrieval takes minutes to hours
    → Data EXISTS but may take time to retrieve.

  Key difference:
    → Durability = data is safe
    → Availability = data is accessible NOW
```

---

<a id="storage-classes"></a>
## Storage Classes — When to Use Each

```
┌────────────────────┬──────────┬──────────────┬────────────┬───────────────┐
│ Storage Class       │ Cost/GB  │ Retrieval    │ Min Store  │ Best For      │
│                     │ /month   │ Time         │ Duration   │               │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Standard            │ $0.023   │ Instant      │ None       │ Active data   │
│                     │          │              │            │ (websites,    │
│                     │          │              │            │ apps)         │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Standard-IA         │ $0.0125  │ Instant      │ 30 days    │ Infrequent    │
│ (Infrequent Access) │          │              │            │ access        │
│                     │          │              │            │ (backups)     │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ One Zone-IA         │ $0.01    │ Instant      │ 30 days    │ Re-creatable  │
│                     │          │              │            │ data (thumbs) │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Intelligent         │ $0.023   │ Instant      │ None       │ Unknown       │
│ Tiering             │ (auto)   │              │            │ access        │
│                     │ adjusts  │              │            │ patterns      │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Glacier Instant     │ $0.004   │ Instant      │ 90 days    │ Archives that │
│ Retrieval           │          │              │            │ need instant  │
│                     │          │              │            │ access        │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Glacier Flexible    │ $0.0036  │ 1-5 min      │ 90 days    │ Backups,      │
│ Retrieval           │          │ (or hours)   │            │ compliance    │
├────────────────────┼──────────┼──────────────┼────────────┼───────────────┤
│ Glacier Deep        │ $0.00099 │ 12-48 hours  │ 180 days   │ Long-term     │
│ Archive             │          │              │            │ archives      │
└────────────────────┴──────────┴──────────────┴────────────┴───────────────┘

COST EXAMPLE: 1TB of data for 1 year
  Standard:           $23.00/month = $276/year
  Standard-IA:        $12.50/month = $150/year
  Glacier Deep:       $0.99/month = $11.88/year

  → Deep Archive is 23x cheaper than Standard!
  → But retrieval takes 12 hours.
```

### Cost Optimization Strategy

```
  TYPICAL LIFECYCLE for a user-uploaded video:

  Day 0:       Upload → Standard ($0.023/GB)
  Day 30:      → Move to Standard-IA ($0.0125/GB) [infrequent access]
  Day 90:      → Move to Glacier Flexible ($0.0036/GB) [archive]
  Day 365:     → Move to Glacier Deep Archive ($0.00099/GB) [long-term]
  Year 7:      → Delete (if compliance allows)

  AUTOMATE WITH LIFECYCLE POLICIES (see below).
```

---

<a id="consistency"></a>
## Consistency Model

### The Old Model (Pre-Dec 2020)

```
BEFORE December 2020:
  
  New object (PUT):
    → Read-after-write consistency
    → PUT then immediately GET → works ✓

  Overwrite (PUT over existing):
    → Eventual consistency
    → PUT new version then immediately GET → might return OLD version
    → Takes ~1-2 seconds to propagate

  Delete:
    → Eventual consistency
    → DELETE then immediately GET → might return the object

This was confusing and caused bugs.
```

### The New Model (Dec 2020+) — Strong Consistency

```
AFTER December 2020:
  
  ALL operations are strongly consistent (read-after-write):

    PUT (new)     → immediate GET returns new object ✓
    PUT (overwrite)→ immediate GET returns updated object ✓
    DELETE        → immediate GET returns 404 Not Found ✓

  S3 now provides strong read-after-write consistency for all operations.
  → No more stale reads.
  → No more eventual consistency confusion.
```

---

<a id="versioning"></a>
## Versioning

```
Without versioning:
  PUT file.txt (v1: "Hello")
  PUT file.txt (v2: "World")  ← overwrites v1
  → v1 is GONE forever.

With versioning enabled:
  PUT file.txt → creates version [version-id: abc123]
  PUT file.txt → creates version [version-id: def456]
  → Both versions stored!

  DELETE file.txt → adds a DELETE MARKER (doesn't actually delete)
  → Object appears deleted, but previous versions are retained.

  Bucket: my-bucket
  ┌─────────────────────────────────────────────────────┐
  │  Key: file.txt                                       │
  │  ├── Version abc123 (current): "World"              │
  │  ├── Version def456: "Hello"                        │
  │  └── DELETE MARKER (if deleted)                      │
  └─────────────────────────────────────────────────────┘

  Recover deleted file:
    DELETE the delete marker → restores previous version

  Use cases:
    → Undo accidental overwrites or deletes
    → Compliance (retain all versions of a document)
    → MFA Delete: require MFA code to delete versions
      → Prevents accidental or malicious deletion
```

---

<a id="lifecycle"></a>
## Lifecycle Policies

Automatically transition objects between storage classes or delete them.

```json
{
  "Rules": [
    {
      "ID": "Move old logs to Glacier",
      "Status": "Enabled",
      "Filter": { "Prefix": "logs/" },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "STANDARD_IA"
        },
        {
          "Days": 90,
          "StorageClass": "GLACIER"
        },
        {
          "Days": 365,
          "StorageClass": "DEEP_ARCHIVE"
        }
      ],
      "Expiration": {
        "Days": 2555    // Delete after 7 years
      }
    }
  ]
}

→ Objects in logs/ automatically move to cheaper storage over time
→ No manual intervention. Saves thousands of dollars.
```

---

<a id="performance"></a>
## Performance — Prefixes, Multipart Upload, Range Gets

### Prefix Performance

```
S3 scales horizontally by partitioning by key prefix.

  OLD LIMITATION (pre-2018):
    Each prefix handled by one partition
    → 3,500 PUT/sec and 5,500 GET/sec per prefix
    → If all keys start with "img/" → bottleneck at 3,500 writes

  CURRENT (2018+):
    S3 auto-partitions based on load
    → Can achieve thousands of requests/sec per prefix
    → If you need more: use diverse prefixes

  BEST PRACTICE for high-throughput uploads:
    Use random prefixes:

    BAD:  img/0001.jpg, img/0002.jpg, img/0003.jpg
    → All in same prefix → limited parallelism

    GOOD: 0/img/0001.jpg, 3/img/0002.jpg, 7/img/0003.jpg
    → Distributed across prefixes → parallel writes
    → Hash the filename → use first character as prefix
```

### Multipart Upload

```
For large files (>100MB), use multipart upload:

  1. INITIATE: Client tells S3 "starting multipart upload"
     → S3 returns upload ID

  2. UPLOAD PARTS: Split file into parts (5MB - 5GB each)
     → Upload each part independently (parallel!)
     → Each part gets an ETag

     Part 1 (bytes 0-5MB)    ──► S3  (parallel)
     Part 2 (bytes 5-10MB)   ──► S3  (parallel)
     Part 3 (bytes 10-15MB)  ──► S3  (parallel)
     Part 4 (bytes 15-20MB)  ──► S3  (parallel)

  3. COMPLETE: Send list of parts + ETags
     → S3 assembles the parts into one object

  Benefits:
    → Parallel upload (5-10x faster)
    → Retry only failed parts (not entire file)
    → Pause and resume uploads
    → Required for files > 5GB
```

### Byte-Range Fetches

```
GET only a portion of an object:

  GET /bucket/video.mp4
  Range: bytes=0-1023       → First 1KB
  Range: bytes=1024-2047    → Next 1KB

  Use cases:
    → Video streaming: fetch first 1MB to start playback fast
    → Partial file recovery: download just the corrupt section
    → Parallel downloads: 10 threads each fetch 1/10 of the file
```

---

<a id="security"></a>
## Security

### Bucket Policies

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::123456789:user/app-user" },
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::my-bucket/*"
    }
  ]
}
```

### Presigned URLs (Time-Limited Access)

```
  Scenario: User needs to upload a video directly to S3
            (without going through your server)

  WITHOUT presigned URL:
    User → Your server → S3
    → Server receives entire video (bandwidth cost, slow)

  WITH presigned URL:
    1. Server generates presigned URL:
       URL = https://my-bucket.s3.amazonaws.com/video.mp4
             ?X-Amz-Signature=abc123...
             &X-Amz-Expires=3600
       → Valid for 1 hour

    2. Server gives URL to client

    3. Client uploads DIRECTLY to S3:
       PUT https://my-bucket.s3.amazonaws.com/video.mp4?X-Amz-Signature=...
       → Bypasses your server entirely
       → Saves bandwidth, faster for user

    4. Client tells your server "upload done"
       → Server processes the video
```

### Encryption

```
  3 encryption options:

  SSE-S3 (Server-side, S3-managed keys):
    → S3 encrypts/decrypts with its own keys
    → Simplest. Transparent to application.
    → Free.

  SSE-KMS (Server-side, KMS-managed keys):
    → AWS KMS manages encryption keys
    → Key rotation, access logging, audit trail
    → Costs money ($1 per million requests)
    → Best for sensitive data (PII, financial)

  Client-side encryption:
    → Client encrypts before uploading
    → S3 never sees plaintext
    → Maximum security. Most complex.
```

---

<a id="events"></a>
## Event Notifications

```
S3 can trigger actions when objects are created/deleted:

  Upload to S3
    │
    ├──► SNS (notification fanout)
    │     → Email subscribers
    │     → SMS subscribers
    │
    ├──► SQS (queue)
    │     → Worker processes image thumbnails
    │
    └──► Lambda (serverless function)
          → Transcode video
          → Generate thumbnail
          → Update database
          → Index in Elasticsearch

  Example flow (Instagram-style photo upload):

  1. User uploads photo → S3 (original)
  2. S3 event → Lambda "GenerateThumbnail"
     → Creates 150x150 thumbnail
     → Saves to S3 (thumbnail)
  3. S3 event → Lambda "UpdateDatabase"
     → Updates PostgreSQL with photo metadata
  4. S3 event → Lambda "IndexSearch"
     → Adds to Elasticsearch for caption search

  All automatic. Zero server infrastructure.
```

---

<a id="real-apps"></a>
## How Real Companies Use S3

| Company | What They Store | Scale |
|---------|----------------|-------|
| **Netflix** | Movie files, thumbnails, artwork | 200+ PB on S3 |
| **Airbnb** | Property photos, user avatars | Billions of objects |
| **Amazon** | Product images, reviews data | Exabytes |
| **Pinterest** | Pin images, board thumbnails | 100+ PB |
| **Twitter** | Media attachments, video clips | 50+ PB |
| **Reddit** | Image uploads, video | 10+ PB |
| **Snapchat** | Snap memories (photos, videos) | 100+ PB |
| **Dropbox** | File storage backend (S3 underneath) | 500+ PB |

### Netflix Example

```
Netflix stores movie files on S3, served via their own CDN:

  1. Netflix encodes movie into multiple resolutions
     → 4K, 1080p, 720p, 480p versions
     → Each version stored as separate S3 object

  2. Movie files are very large (4K movie = 50GB+)
     → Use multipart upload for reliability

  3. Open Connect CDN fetches from S3:
     S3 → Open Connect Appliance (at ISP) → User

  4. Thumbnails and artwork:
     → Stored on S3
     → Served via CloudFront CDN

  5. Analytics:
     → Viewing events stored on S3 as JSON/Parquet
     → Queried by Athena (SQL on S3) or loaded to Redshift
```

---

<a id="build"></a>
## How YOU Can Build This

### AWS CLI

```bash
# Create bucket
aws s3 mb s3://my-app-bucket

# Upload file
aws s3 cp photo.jpg s3://my-app-bucket/images/photo.jpg

# Download file
aws s3 cp s3://my-app-bucket/images/photo.jpg ./photo.jpg

# List objects
aws s3 ls s3://my-app-bucket/images/

# Delete
aws s3 rm s3://my-app-bucket/images/photo.jpg

# Sync (like rsync)
aws s3 sync ./local-dir/ s3://my-app-bucket/dir/
```

### Python (boto3)

```python
import boto3
from botocore.client import Config

s3 = boto3.client('s3', region_name='us-east-1')

# Upload
s3.upload_file('local_video.mp4', 'my-bucket', 'videos/video.mp4')

# Download
s3.download_file('my-bucket', 'videos/video.mp4', 'downloaded.mp4')

# Generate presigned URL for upload (client uploads directly)
url = s3.generate_presigned_url(
    'put_object',
    Params={'Bucket': 'my-bucket', 'Key': 'uploads/user123_photo.jpg'},
    ExpiresIn=3600  # 1 hour
)
print(f"Upload URL: {url}")
# Client: requests.put(url, data=file_bytes)

# Generate presigned URL for download
url = s3.generate_presigned_url(
    'get_object',
    Params={'Bucket': 'my-bucket', 'Key': 'videos/video.mp4'},
    ExpiresIn=86400  # 24 hours
)
print(f"Download URL: {url}")
```

### Multipart Upload (Python)

```python
import boto3

s3 = boto3.client('s3')

# For large files, use transfer manager (auto-multipart)
transfer = boto3.s3.transfer.S3Transfer(
    s3,
    config=boto3.s3.transfer.TransferConfig(
        multipart_threshold=8 * 1024 * 1024,   # Use multipart for >8MB
        max_concurrency=10,                     # 10 parallel uploads
        multipart_chunksize=8 * 1024 * 1024,   # 8MB per part
    )
)

# Upload (automatically uses multipart for large files)
transfer.upload_file('large_video.mp4', 'my-bucket', 'videos/large.mp4')
```

---

## Common Interview Questions

**Q: How does S3 achieve 99.999999999% durability?**

A: Redundancy across multiple availability zones. Each object is stored on multiple disks across 3+ data centers. S3 also performs background scrubbing — continuously scanning stored data for bit rot (silent corruption) and repairing corrupted copies from healthy ones. Checksums are verified on every read. With 6+ copies across 3 AZs, the probability of all copies failing simultaneously is astronomically low — resulting in 11 nines of durability (losing 1 object per 10,000 years per 10 million objects).

**Q: Explain S3 storage classes and when to use each.**

A: Standard ($0.023/GB) for active data accessed frequently. Standard-IA ($0.0125/GB) for infrequent access but instant retrieval when needed — good for backups accessed monthly. Glacier Flexible ($0.0036/GB) for archival data with 1-5 minute retrieval — compliance archives. Glacier Deep Archive ($0.00099/GB) for long-term retention with 12-hour retrieval — 23x cheaper than Standard. Intelligent Tiering automatically moves objects between classes based on access patterns. Use lifecycle policies to automate transitions.

**Q: What is a presigned URL and why use it?**

A: A time-limited URL that grants temporary access to an S3 object without requiring AWS credentials. The server generates it using its own credentials + an expiry time. The client uses the URL to upload or download directly from S3, bypassing the server. This saves server bandwidth (client → S3 instead of client → server → S3), reduces latency, and offloads work from the application server.

**Q: How does S3 handle very large files?**

A: Multipart upload. The file is split into parts (5MB to 5GB each), uploaded independently and in parallel. Benefits: (1) parallel upload for speed, (2) retry only failed parts (not entire file), (3) pause and resume capability. Required for files > 5GB. On the read side, byte-range requests allow fetching specific portions of a large object — useful for video streaming (fetch first chunk to start playback) or parallel downloads.

**Q: How do you optimize S3 performance for high throughput?**

A: Use diverse key prefixes. S3 partitions by prefix, so all keys with the same prefix share one partition's throughput. By using random or hash-based prefixes (e.g., "0/img/...", "3/img/...", "7/img/..."), you distribute keys across partitions, achieving higher aggregate throughput. Also use multipart upload for parallel writes, and byte-range fetches for parallel reads. S3 now auto-partitions based on load, but diverse prefixes still help for extreme throughput.

-- ═══════════════════════════════════════════════════════════════
-- NebulaShop Database Schema
-- ═══════════════════════════════════════════════════════════════

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50) UNIQUE NOT NULL,
    email       VARCHAR(255) UNIQUE NOT NULL,
    bio         TEXT DEFAULT '',
    avatar_url  VARCHAR(500),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Posts table
CREATE TABLE IF NOT EXISTS posts (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    title       VARCHAR(200) NOT NULL,
    content     TEXT NOT NULL,
    views       INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    product     VARCHAR(200) NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    price       DECIMAL(10, 2) NOT NULL,
    status      VARCHAR(20) DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);

-- Insert sample data
INSERT INTO users (username, email, bio) VALUES
    ('alice', 'alice@nebula.shop', 'Coffee enthusiast and code writer'),
    ('bob', 'bob@nebula.shop', 'Photographer | Traveler'),
    ('carol', 'carol@nebula.shop', 'DevOps engineer who loves Kubernetes')
ON CONFLICT (username) DO NOTHING;

INSERT INTO posts (user_id, title, content) VALUES
    (1, 'Building Scalable APIs with Flask', 'Today I want to share how to build APIs that handle 10K requests per second using Flask, Redis caching, and PostgreSQL connection pooling...'),
    (2, 'My Favorite Photography Spots in Mumbai', 'Mumbai has incredible light at sunrise. Here are my top 5 spots for street photography...'),
    (3, 'Kubernetes Made Simple', 'Kubernetes doesn''t have to be complicated. Let me explain pods, services, and deployments in plain English...'),
    (1, 'Redis vs Memcached: When to Choose What', 'Both are in-memory caches, but they serve different needs. Here''s my decision framework...'),
    (3, 'Monitoring Everything with Prometheus', 'If you can''t measure it, you can''t improve it. Here''s how to instrument your apps with Prometheus metrics...')
ON CONFLICT DO NOTHING;

INSERT INTO orders (user_id, product, quantity, price) VALUES
    (1, 'NebulaShop Coffee Mug', 2, 499.00),
    (2, 'Programming Sticker Pack', 1, 299.00),
    (3, 'Mechanical Keyboard', 1, 4999.00)
ON CONFLICT DO NOTHING;

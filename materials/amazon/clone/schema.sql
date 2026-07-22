PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_products (
    asin TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    brand TEXT NOT NULL,
    capacity TEXT NOT NULL,
    color TEXT NOT NULL,
    price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
    list_price_minor INTEGER,
    currency TEXT NOT NULL,
    rating TEXT NOT NULL,
    reviews INTEGER NOT NULL CHECK (reviews >= 0),
    image_path TEXT NOT NULL,
    badge TEXT NOT NULL DEFAULT '',
    evidence_class TEXT NOT NULL
);

-- Transaction-ready offers are intentionally separate from the frozen search
-- catalog.  This lets current direct-PDP evidence override an older search
-- offer without changing the benchmark's nine-result catalog contract.
CREATE TABLE IF NOT EXISTS commerce_offers (
    asin TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    title TEXT NOT NULL,
    brand TEXT NOT NULL,
    capacity TEXT NOT NULL,
    color TEXT NOT NULL,
    price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
    list_price_minor INTEGER CHECK (list_price_minor IS NULL OR list_price_minor >= 0),
    currency TEXT NOT NULL,
    rating TEXT NOT NULL,
    reviews INTEGER NOT NULL CHECK (reviews >= 0),
    image_path TEXT NOT NULL,
    badge TEXT NOT NULL DEFAULT '',
    evidence_class TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('task-fixture', 'direct-pdp'))
);

CREATE TABLE IF NOT EXISTS product_details (
    asin TEXT PRIMARY KEY REFERENCES catalog_products(asin) ON DELETE CASCADE,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_normalized TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    password_scheme TEXT NOT NULL CHECK (password_scheme = 'scrypt-v1'),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_lists (
    list_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    canonical_path TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ranking_items (
    list_id TEXT NOT NULL REFERENCES ranking_lists(list_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL CHECK (rank > 0),
    asin TEXT NOT NULL REFERENCES catalog_products(asin),
    PRIMARY KEY (list_id, rank),
    UNIQUE (list_id, asin)
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    session_digest TEXT PRIMARY KEY,
    reset_epoch INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(account_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS compare_items (
    compare_line_id TEXT PRIMARY KEY NOT NULL,
    session_digest TEXT NOT NULL REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    selection_json TEXT NOT NULL DEFAULT '{}',
    selection_key TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 4),
    created_at TEXT NOT NULL,
    UNIQUE (session_digest, asin, selection_key),
    UNIQUE (session_digest, position)
);

CREATE TABLE IF NOT EXISTS account_compare_items (
    compare_line_id TEXT PRIMARY KEY NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    selection_json TEXT NOT NULL DEFAULT '{}',
    selection_key TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 4),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, asin, selection_key),
    UNIQUE (account_id, position)
);

CREATE TABLE IF NOT EXISTS auth_signin_flows (
    session_digest TEXT PRIMARY KEY REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL,
    return_to TEXT,
    updated_at TEXT NOT NULL
);

-- Persistent delivery budgets survive replacement of an auth flow. Recipient
-- keys are SHA-256 digests rather than email addresses.
CREATE TABLE IF NOT EXISTS auth_mail_rate_limits (
    purpose TEXT NOT NULL CHECK (purpose IN ('registration', 'password-reset')),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('session', 'recipient', 'account')),
    scope_key TEXT NOT NULL,
    window_started_at INTEGER NOT NULL,
    send_count INTEGER NOT NULL CHECK (send_count >= 0),
    last_sent_at INTEGER NOT NULL,
    PRIMARY KEY (purpose, scope_type, scope_key)
);

-- Registration credentials remain pending until the email one-time code is
-- verified. Password material is already salted/hashed at this stage; the
-- public storefront never stores a pending plaintext password or OTP.
CREATE TABLE IF NOT EXISTS auth_registration_flows (
    pending_id TEXT PRIMARY KEY,
    session_digest TEXT NOT NULL UNIQUE REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL,
    display_name TEXT NOT NULL,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    password_scheme TEXT NOT NULL CHECK (password_scheme = 'scrypt-v1'),
    return_to TEXT,
    code_salt BLOB NOT NULL,
    code_hash BLOB NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Verification mail is LOCAL_ONLY unless an operator configures SMTP. Codes
-- are exposed only for LOCAL_ONLY delivery by the token-protected admin server.
CREATE TABLE IF NOT EXISTS auth_registration_email_outbox (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_id TEXT NOT NULL UNIQUE REFERENCES auth_registration_flows(pending_id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    template TEXT NOT NULL CHECK (template = 'registration-verification'),
    verification_code TEXT NOT NULL CHECK (
        length(verification_code) = 6
        AND verification_code NOT GLOB '*[^0-9]*'
    ),
    status TEXT NOT NULL CHECK (status IN (
        'LOCAL_ONLY', 'SMTP_PENDING', 'SMTP_SENT', 'SMTP_FAILED'
    )),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation IN (0, 1)),
    delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    claim_token TEXT,
    last_error TEXT,
    attempted_at INTEGER,
    sent_at INTEGER,
    created_at INTEGER NOT NULL,
    CHECK (
        (status = 'LOCAL_ONLY' AND is_simulation = 1)
        OR (status <> 'LOCAL_ONLY' AND is_simulation = 0)
    )
);

-- Password reset state is session-bound. account_id is intentionally nullable
-- so unknown identifiers follow the same public flow without receiving mail.
CREATE TABLE IF NOT EXISTS auth_password_reset_flows (
    reset_id TEXT PRIMARY KEY,
    session_digest TEXT NOT NULL UNIQUE REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    account_id INTEGER REFERENCES accounts(account_id) ON DELETE CASCADE,
    return_to TEXT,
    code_salt BLOB NOT NULL,
    code_hash BLOB NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    verified_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS auth_password_reset_account_unique_idx
    ON auth_password_reset_flows(account_id)
    WHERE account_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS auth_password_reset_email_outbox (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_id TEXT NOT NULL UNIQUE REFERENCES auth_password_reset_flows(reset_id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    template TEXT NOT NULL CHECK (template = 'password-reset-verification'),
    verification_code TEXT NOT NULL CHECK (
        length(verification_code) = 6
        AND verification_code NOT GLOB '*[^0-9]*'
    ),
    status TEXT NOT NULL CHECK (status IN (
        'LOCAL_ONLY', 'SMTP_PENDING', 'SMTP_SENT', 'SMTP_FAILED'
    )),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation IN (0, 1)),
    delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    claim_token TEXT,
    last_error TEXT,
    attempted_at INTEGER,
    sent_at INTEGER,
    created_at INTEGER NOT NULL,
    CHECK (
        (status = 'LOCAL_ONLY' AND is_simulation = 1)
        OR (status <> 'LOCAL_ONLY' AND is_simulation = 0)
    )
);

CREATE TABLE IF NOT EXISTS carts (
    cart_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_digest TEXT NOT NULL UNIQUE REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_lines (
    line_id TEXT PRIMARY KEY NOT NULL,
    cart_id INTEGER NOT NULL REFERENCES carts(cart_id) ON DELETE CASCADE,
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
    selection_json TEXT NOT NULL DEFAULT '{}',
    selection_key TEXT NOT NULL,
    line_state TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (line_state IN ('ACTIVE', 'SAVED')),
    UNIQUE (cart_id, asin, selection_key)
);

-- Authenticated carts are account-owned so they survive sign-out, session
-- rotation, and a later sign-in from another browser session. Anonymous carts
-- stay in carts/cart_lines until authentication merges them transactionally.
CREATE TABLE IF NOT EXISTS account_cart_lines (
    line_id TEXT PRIMARY KEY NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
    selection_json TEXT NOT NULL DEFAULT '{}',
    selection_key TEXT NOT NULL,
    line_state TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (line_state IN ('ACTIVE', 'SAVED')),
    UNIQUE (account_id, asin, selection_key)
);

-- A guest Buy Now intent must survive the sign-in/registration session
-- rotation without being mixed into the shopper's ordinary cart.
CREATE TABLE IF NOT EXISTS pending_buy_now (
    session_digest TEXT PRIMARY KEY
        REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
    selection_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
    address_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    address_line1 TEXT NOT NULL,
    address_line2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL,
    state_region TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    country_code TEXT NOT NULL CHECK (
        length(country_code) = 2 AND country_code = upper(country_code)
    ),
    phone TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (is_archived = 0 OR is_default = 0)
);

CREATE TABLE IF NOT EXISTS checkout_sessions (
    checkout_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    checkout_mode TEXT NOT NULL DEFAULT 'CART'
        CHECK (checkout_mode IN ('CART', 'BUY_NOW')),
    status TEXT NOT NULL CHECK (status IN (
        'CART_READY', 'ADDRESS_SELECTED', 'DELIVERY_SELECTED',
        'PAYMENT_SELECTED', 'PLACED'
    )),
    address_id INTEGER REFERENCES addresses(address_id),
    delivery_method TEXT CHECK (
        delivery_method IS NULL OR delivery_method IN ('standard', 'expedited')
    ),
    shipping_minor INTEGER CHECK (
        shipping_minor IS NULL OR shipping_minor IN (0, 1299)
    ),
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    placed_at TEXT,
    CHECK (
        (status = 'CART_READY' AND address_id IS NULL
            AND delivery_method IS NULL AND shipping_minor IS NULL
            AND placed_at IS NULL)
        OR
        (status = 'ADDRESS_SELECTED' AND address_id IS NOT NULL
            AND delivery_method IS NULL AND shipping_minor IS NULL
            AND placed_at IS NULL)
        OR
        (status IN ('DELIVERY_SELECTED', 'PAYMENT_SELECTED')
            AND address_id IS NOT NULL AND delivery_method IS NOT NULL
            AND shipping_minor IS NOT NULL AND placed_at IS NULL)
        OR
        (status = 'PLACED' AND address_id IS NOT NULL
            AND delivery_method IS NOT NULL AND shipping_minor IS NOT NULL
            AND placed_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS checkout_sessions_one_open_account_idx
    ON checkout_sessions(account_id) WHERE status <> 'PLACED';
CREATE UNIQUE INDEX IF NOT EXISTS checkout_sessions_idempotency_key_idx
    ON checkout_sessions(idempotency_key);

-- BUY_NOW checkouts own an isolated line snapshot.  CART checkouts continue
-- to read the account cart so cart edits invalidate stale payment approvals.
CREATE TABLE IF NOT EXISTS checkout_lines (
    checkout_id INTEGER NOT NULL
        REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
    selection_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (checkout_id, ordinal)
);

CREATE TABLE IF NOT EXISTS payment_attempts (
    payment_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkout_id INTEGER NOT NULL REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    method TEXT NOT NULL CHECK (method IN (
        'test-card','sandbox-card-approved','sandbox-card-declined',
        'sandbox-bank-approved'
    )),
    status TEXT NOT NULL CHECK (status IN ('APPROVED', 'DECLINED', 'SUPERSEDED')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    cart_fingerprint TEXT NOT NULL,
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_one_approved_checkout_idx
    ON payment_attempts(checkout_id) WHERE status = 'APPROVED';

CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    checkout_id INTEGER NOT NULL UNIQUE REFERENCES checkout_sessions(checkout_id),
    payment_attempt_id INTEGER NOT NULL UNIQUE REFERENCES payment_attempts(payment_attempt_id),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'PLACED'),
    items_subtotal_minor INTEGER NOT NULL CHECK (items_subtotal_minor >= 0),
    shipping_minor INTEGER NOT NULL CHECK (shipping_minor IN (0, 1299)),
    total_minor INTEGER NOT NULL CHECK (
        total_minor >= 0 AND total_minor = items_subtotal_minor + shipping_minor
    ),
    currency TEXT NOT NULL,
    delivery_method TEXT NOT NULL CHECK (
        delivery_method IN ('standard', 'expedited')
    ),
    shipping_address_json TEXT NOT NULL,
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS orders_account_created_idx
    ON orders(account_id, order_id DESC);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    asin TEXT NOT NULL,
    title TEXT NOT NULL,
    image_path TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
    selection_json TEXT NOT NULL DEFAULT '{}',
    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
    line_total_minor INTEGER NOT NULL CHECK (
        line_total_minor = unit_price_minor * quantity
    ),
    currency TEXT NOT NULL,
    UNIQUE (order_id, ordinal)
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status = 'PREPARING'),
    lifecycle_status TEXT NOT NULL DEFAULT 'PREPARING' CHECK (
        lifecycle_status IN ('PREPARING', 'SHIPPED', 'DELIVERED', 'CANCELLED')
    ),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    delivery_method TEXT NOT NULL CHECK (
        delivery_method IN ('standard', 'expedited')
    ),
    shipping_minor INTEGER NOT NULL CHECK (shipping_minor IN (0, 1299)),
    carrier TEXT NOT NULL,
    tracking_code TEXT,
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    shipped_at TEXT,
    delivered_at TEXT,
    cancelled_at TEXT,
    CHECK (
        (lifecycle_status IN ('PREPARING', 'CANCELLED') AND tracking_code IS NULL)
        OR
        (lifecycle_status IN ('SHIPPED', 'DELIVERED') AND tracking_code IS NOT NULL
            AND length(tracking_code) BETWEEN 8 AND 64)
    ),
    CHECK (lifecycle_status<>'SHIPPED' OR shipped_at IS NOT NULL),
    CHECK (lifecycle_status<>'DELIVERED' OR (shipped_at IS NOT NULL AND delivered_at IS NOT NULL)),
    CHECK (lifecycle_status<>'CANCELLED' OR cancelled_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS order_events (
    order_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'ORDER_PLACED', 'ORDER_CANCELLED', 'SHIPMENT_SHIPPED',
        'SHIPMENT_DELIVERED', 'RETURN_REQUESTED', 'RETURN_RECEIVED',
        'RETURN_REFUNDED'
    )),
    actor TEXT NOT NULL CHECK (actor IN ('CUSTOMER', 'ADMIN', 'SYSTEM')),
    from_status TEXT,
    to_status TEXT NOT NULL,
    idempotency_key TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (order_id, event_type)
);

CREATE INDEX IF NOT EXISTS order_events_order_idx
    ON order_events(order_id, order_event_id);
CREATE UNIQUE INDEX IF NOT EXISTS order_events_mutation_key_idx
    ON order_events(account_id, event_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS order_action_keys (
    order_action_key_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    action_type TEXT NOT NULL CHECK (action_type IN ('CANCEL', 'RETURN_REQUEST')),
    idempotency_key TEXT NOT NULL,
    result_reference INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, action_type, idempotency_key),
    UNIQUE (order_id, action_type)
);

CREATE TABLE IF NOT EXISTS return_requests (
    return_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code IN (
        'DAMAGED', 'DEFECTIVE', 'NOT_AS_DESCRIBED',
        'WRONG_ITEM', 'NO_LONGER_NEEDED'
    )),
    customer_note TEXT NOT NULL DEFAULT '' CHECK (length(customer_note) <= 500),
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'RECEIVED', 'REFUNDED')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS return_requests_account_idx
    ON return_requests(account_id, return_request_id DESC);

CREATE TABLE IF NOT EXISTS return_request_items (
    return_request_id INTEGER NOT NULL
        REFERENCES return_requests(return_request_id) ON DELETE CASCADE,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (return_request_id, order_item_id)
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    payment_attempt_id INTEGER NOT NULL REFERENCES payment_attempts(payment_attempt_id),
    return_request_id INTEGER REFERENCES return_requests(return_request_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('CANCELLATION', 'RETURN')),
    status TEXT NOT NULL CHECK (status = 'COMPLETED'),
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
    created_at TEXT NOT NULL,
    CHECK (
        (kind='CANCELLATION' AND return_request_id IS NULL)
        OR (kind='RETURN' AND return_request_id IS NOT NULL)
    ),
    UNIQUE (order_id, kind),
    UNIQUE (account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS refunds_account_idx
    ON refunds(account_id, refund_id DESC);

CREATE TABLE IF NOT EXISTS email_outbox (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    template TEXT NOT NULL CHECK (template = 'order-confirmation'),
    subject TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'LOCAL_ONLY', 'SMTP_PENDING', 'SMTP_SENT', 'SMTP_FAILED'
    )),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation IN (0, 1)),
    delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    claim_token TEXT,
    last_error TEXT,
    attempted_at INTEGER,
    sent_at INTEGER,
    created_at TEXT NOT NULL,
    CHECK (
        (status = 'LOCAL_ONLY' AND is_simulation = 1)
        OR (status <> 'LOCAL_ONLY' AND is_simulation = 0)
    )
);

CREATE TABLE IF NOT EXISTS navigation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_digest TEXT NOT NULL REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    method TEXT NOT NULL,
    route_key TEXT NOT NULL,
    path TEXT NOT NULL,
    referer TEXT NOT NULL,
    status INTEGER NOT NULL,
    asin TEXT,
    rank INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (session_digest, sequence)
);

CREATE TABLE IF NOT EXISTS task_progress (
    session_digest TEXT PRIMARY KEY REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    best_sellers_event_id INTEGER REFERENCES navigation_events(event_id),
    pdp_event_id INTEGER REFERENCES navigation_events(event_id),
    flow_capability_digest TEXT,
    capability_consumed INTEGER NOT NULL DEFAULT 0 CHECK (capability_consumed IN (0, 1)),
    reset_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS request_journal (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_digest TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    raw_body_sha256 TEXT NOT NULL,
    canonical_form TEXT NOT NULL,
    status INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_completions (
    completion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    session_digest TEXT NOT NULL,
    task_id TEXT NOT NULL,
    terminal_path TEXT NOT NULL,
    request_id INTEGER NOT NULL REFERENCES request_journal(request_id),
    completed_at TEXT NOT NULL,
    UNIQUE (run_id, session_digest, task_id)
);

CREATE INDEX IF NOT EXISTS navigation_events_session_idx
    ON navigation_events(session_digest, sequence);
CREATE INDEX IF NOT EXISTS request_journal_session_idx
    ON request_journal(session_digest, request_id);

from __future__ import annotations


SCHEMA_VERSION = 10


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    file_ext TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    status TEXT NOT NULL DEFAULT 'unused',
    tags TEXT NOT NULL DEFAULT '[]',
    batch_name TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_sha256 ON videos(sha256);
CREATE INDEX IF NOT EXISTS idx_videos_file_name ON videos(file_name);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_batch_name ON videos(batch_name);
CREATE INDEX IF NOT EXISTS idx_videos_imported_at ON videos(imported_at);

CREATE TABLE IF NOT EXISTS captions (
    caption_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unused',
    tags TEXT NOT NULL DEFAULT '[]',
    platform TEXT NOT NULL DEFAULT '',
    account_scope TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS caption_video_bindings (
    binding_id TEXT PRIMARY KEY,
    caption_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(caption_id, video_id),
    FOREIGN KEY(caption_id) REFERENCES captions(caption_id),
    FOREIGN KEY(video_id) REFERENCES videos(video_id)
);

CREATE INDEX IF NOT EXISTS idx_captions_status ON captions(status);
CREATE INDEX IF NOT EXISTS idx_caption_video_bindings_caption ON caption_video_bindings(caption_id);
CREATE INDEX IF NOT EXISTS idx_caption_video_bindings_video ON caption_video_bindings(video_id);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    search_title TEXT NOT NULL,
    publish_name TEXT NOT NULL DEFAULT '',
    product_link TEXT NOT NULL DEFAULT '',
    account_scope TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
CREATE INDEX IF NOT EXISTS idx_products_account_scope ON products(account_scope);
CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at);

CREATE TABLE IF NOT EXISTS phones (
    phone_id TEXT PRIMARY KEY,
    device_name TEXT NOT NULL,
    adb_serial TEXT NOT NULL UNIQUE,
    account_name TEXT NOT NULL DEFAULT '',
    account_type TEXT NOT NULL DEFAULT 'marketing',
    connection_mode TEXT NOT NULL DEFAULT 'usb',
    pairing_status TEXT NOT NULL DEFAULT 'unpaired',
    authorization_status TEXT NOT NULL DEFAULT 'unknown',
    current_status TEXT NOT NULL DEFAULT 'offline',
    app_package TEXT NOT NULL DEFAULT '',
    remote_video_dir TEXT NOT NULL DEFAULT '/sdcard/DCIM/Camera',
    last_online_at TEXT,
    daily_publish_count INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS phone_health_checks (
    check_id TEXT PRIMARY KEY,
    phone_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    adb_online INTEGER NOT NULL DEFAULT 0,
    authorized INTEGER NOT NULL DEFAULT 0,
    screenshot_ok INTEGER NOT NULL DEFAULT 0,
    app_detected INTEGER NOT NULL DEFAULT 0,
    media_dir_writable INTEGER NOT NULL DEFAULT 0,
    current_foreground_package TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE INDEX IF NOT EXISTS idx_phones_status ON phones(current_status);
CREATE INDEX IF NOT EXISTS idx_phones_pairing_status ON phones(pairing_status);
CREATE INDEX IF NOT EXISTS idx_phone_health_checks_phone ON phone_health_checks(phone_id);
CREATE INDEX IF NOT EXISTS idx_phone_health_checks_checked_at ON phone_health_checks(checked_at);
CREATE TABLE IF NOT EXISTS release_tasks (
    task_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    caption_id TEXT,
    phone_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    publish_mode TEXT NOT NULL DEFAULT 'scheduled',
    product_id TEXT NOT NULL DEFAULT '',
    product_link TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    product_search_title TEXT NOT NULL DEFAULT '',
    product_publish_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    note TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    run_dir TEXT NOT NULL DEFAULT '',
    locked_by_worker TEXT NOT NULL DEFAULT '',
    lock_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(video_id),
    FOREIGN KEY(caption_id) REFERENCES captions(caption_id),
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE INDEX IF NOT EXISTS idx_release_tasks_status ON release_tasks(status);
CREATE INDEX IF NOT EXISTS idx_release_tasks_scheduled_at ON release_tasks(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_release_tasks_phone_id ON release_tasks(phone_id);
CREATE INDEX IF NOT EXISTS idx_release_tasks_video_id ON release_tasks(video_id);
CREATE INDEX IF NOT EXISTS idx_release_tasks_caption_id ON release_tasks(caption_id);

CREATE TABLE IF NOT EXISTS execution_logs (
    log_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    phone_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    result TEXT NOT NULL DEFAULT 'running',
    failed_step TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    state_path TEXT NOT NULL DEFAULT '',
    trace_dir TEXT NOT NULL DEFAULT '',
    screenshot_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES release_tasks(task_id),
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_logs_task_id ON execution_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_execution_logs_started_at ON execution_logs(started_at);

CREATE TABLE IF NOT EXISTS task_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    phone_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    result TEXT NOT NULL DEFAULT 'running',
    failure_reason TEXT NOT NULL DEFAULT '',
    run_dir TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(task_id) REFERENCES release_tasks(task_id),
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE INDEX IF NOT EXISTS idx_task_attempts_task_id ON task_attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_task_attempts_phone_id ON task_attempts(phone_id);
CREATE INDEX IF NOT EXISTS idx_task_attempts_started_at ON task_attempts(started_at);

CREATE TABLE IF NOT EXISTS execution_workers (
    worker_id TEXT PRIMARY KEY,
    phone_id TEXT NOT NULL,
    adb_serial TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'starting',
    pid INTEGER NOT NULL DEFAULT 0,
    current_task_id TEXT NOT NULL DEFAULT '',
    current_attempt_id TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_workers_phone_id ON execution_workers(phone_id);
CREATE INDEX IF NOT EXISTS idx_execution_workers_status ON execution_workers(status);
CREATE INDEX IF NOT EXISTS idx_execution_workers_lease ON execution_workers(lease_expires_at);

CREATE TABLE IF NOT EXISTS video_usage (
    usage_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    caption_id TEXT,
    phone_id TEXT NOT NULL,
    used_at TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(video_id) REFERENCES videos(video_id),
    FOREIGN KEY(task_id) REFERENCES release_tasks(task_id),
    FOREIGN KEY(caption_id) REFERENCES captions(caption_id),
    FOREIGN KEY(phone_id) REFERENCES phones(phone_id)
);

CREATE TABLE IF NOT EXISTS shared_import_batches (
    batch_id TEXT PRIMARY KEY,
    original_path TEXT NOT NULL DEFAULT '',
    current_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    total_rows INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_import_items (
    import_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    phone_id TEXT NOT NULL DEFAULT '',
    video_file TEXT NOT NULL DEFAULT '',
    video_id TEXT NOT NULL DEFAULT '',
    caption_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    scheduled_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, row_id),
    FOREIGN KEY(batch_id) REFERENCES shared_import_batches(batch_id)
);

CREATE INDEX IF NOT EXISTS idx_shared_import_items_batch ON shared_import_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_shared_import_items_task ON shared_import_items(task_id);
CREATE INDEX IF NOT EXISTS idx_shared_import_items_sha256 ON shared_import_items(sha256);

CREATE TABLE IF NOT EXISTS adb_tool_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    source TEXT NOT NULL DEFAULT 'bundled',
    adb_path TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    service_status TEXT NOT NULL DEFAULT 'unknown',
    platform_tools_status TEXT NOT NULL DEFAULT 'unknown',
    required_files TEXT NOT NULL DEFAULT '{}',
    online_device_count INTEGER NOT NULL DEFAULT 0,
    unauthorized_device_count INTEGER NOT NULL DEFAULT 0,
    offline_device_count INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT
);
"""







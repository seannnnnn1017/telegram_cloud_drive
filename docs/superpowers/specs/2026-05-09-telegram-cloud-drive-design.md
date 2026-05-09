# Telegram Cloud Drive — Design Spec
_Date: 2026-05-09_

## Overview

A personal LAN-hosted cloud drive web app that uses a Telegram bot channel as the file storage backend. The web interface follows the "Vault" minimal dark design: `#0a0a0a` background, `#00ff66` neon-green accent, Inter + JetBrains Mono typography.

Single user, no authentication, LAN-only access.

---

## Architecture

```
telegram_cloud_drive/
├── backend/
│   ├── main.py         # FastAPI app + all endpoint handlers
│   ├── database.py     # SQLite setup & async queries (aiosqlite)
│   ├── telegram.py     # Telegram Bot API wrapper (httpx)
│   └── models.py       # Pydantic request/response models
├── frontend/
│   └── index.html      # Single-page app (Vault dark/green design)
├── .env                # BOT_TOKEN, CHAT_ID
├── requirements.txt
└── README.md
```

### How Telegram Storage Works

1. User creates a **private Telegram channel** and makes the bot an admin with "Delete messages" permission.
2. `chat_id` of that channel is set in `.env`.
3. **Upload**: backend receives file → sends to Telegram channel via `sendDocument` → stores returned `message_id` + `file_id` in SQLite.
4. **Download**: `getFile` returns a Telegram CDN URL → backend proxies the bytes to the browser (avoids CORS issues, keeps a clean URL).
5. **Delete**: `deleteMessage` removes the file from the channel → delete row from SQLite.
6. **Preview**: re-uses the download proxy endpoint with `Content-Disposition: inline`.

### File Size Limit

Standard Telegram Bot API caps uploads at **20 MB**. Files exceeding this size receive a clear frontend error. Users who need larger file support must self-host the Telegram Local Bot API (documented in README).

---

## Database Schema (SQLite)

```sql
CREATE TABLE files (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT    NOT NULL,
  size           INTEGER NOT NULL,       -- bytes
  mime_type      TEXT,
  tg_file_id     TEXT    NOT NULL,
  tg_message_id  INTEGER NOT NULL,
  uploaded_at    TEXT    NOT NULL        -- ISO-8601 UTC
);

CREATE INDEX idx_files_name        ON files(name);
CREATE INDEX idx_files_uploaded_at ON files(uploaded_at);
CREATE INDEX idx_files_mime_type   ON files(mime_type);
```

---

## API Endpoints

| Method   | Path                      | Description                                        |
|----------|---------------------------|----------------------------------------------------|
| GET      | `/api/files`              | List files. Query: `q`, `sort`, `order`, `type`    |
| POST     | `/api/upload`             | Multipart upload (≤20 MB). Returns created file.   |
| GET      | `/api/files/{id}/download`| Proxy download from Telegram CDN.                  |
| GET      | `/api/files/{id}/preview` | Inline preview (images, PDFs). 404 if unsupported. |
| DELETE   | `/api/files/{id}`         | Delete from Telegram + SQLite.                     |
| DELETE   | `/api/files/bulk`         | Bulk delete. Body: `{"ids": [1, 2, 3]}`.           |
| GET      | `/api/storage`            | Returns `{used_bytes, file_count}`.                |

**Query params for `GET /api/files`:**
- `q` — substring search on filename
- `sort` — `name` | `date` | `size` | `type` (default: `date`)
- `order` — `asc` | `desc` (default: `desc`)
- `type` — `image` | `video` | `document` | `other` (omit = all)

---

## Frontend Features

Visual design: matches `Cloud Drive Minimal.html` exactly.

### Layout
- **Header**: "Vault · Personal" brand mark + Upload button + Storage bar (used / total, pulled from `/api/storage`)
- **Toolbar**: Search input (live filter), type tabs (All / Photos / Videos / Docs), Sort button, List/Grid toggle (list view only for MVP)
- **File list table**: columns — Name, Type, Modified, Size, ⋯ menu
- **Footer**: "Synced · Xs ago" live indicator

### Interactions
- **Upload**: Header button + drag-and-drop overlay on the page. Shows progress bar. Rejects files >20 MB with a toast error.
- **Sort**: Click any column header toggles asc/desc.
- **Search**: Debounced input hits `GET /api/files?q=…` and re-renders the list.
- **Tabs**: All / Photos / Videos / Docs — maps to `type` query param.
- **Multi-select**: Checkbox appears on row hover; checking one row enters multi-select mode. A bulk action toolbar slides in at the bottom: "X selected — Delete All / Download All". Clicking outside deselects.
- **Per-file ⋯ menu**: Download, Delete (confirm dialog).
- **Preview modal**: Images displayed inline in a centered overlay. PDFs opened in an `<iframe>` (browser native viewer). All other types trigger download instead of preview.
- **Toasts**: Success/error for upload, delete, bulk actions.

### No Authentication
LAN-only deployment, single user. No login screen, no sessions.

---

## Setup Requirements (README)

1. Python 3.11+
2. Create a private Telegram channel → add bot as admin with "Post messages" + "Delete messages" permissions
3. Copy channel `chat_id` (e.g. `-100xxxxxxxxx`)
4. Create `.env`:
   ```
   BOT_TOKEN=8516455355:AAEfV8VCa5OUS3lvCBPtYMnvuruV7nP3jUU
   CHAT_ID=-100xxxxxxxxx
   ```
5. `pip install -r requirements.txt`
6. `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
7. Open `http://<LAN-IP>:8000`

---

## Out of Scope

- File sharing (no public links)
- User authentication / multi-user
- Folder/directory structure
- Thumbnail generation
- Files >20 MB (flagged with warning, not silently truncated)

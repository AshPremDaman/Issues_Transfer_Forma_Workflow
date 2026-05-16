# Forma Issues Transfer Tool

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-green?logo=flask&logoColor=white)
![Autodesk](https://img.shields.io/badge/Autodesk-Forma%20%2F%20ACC-orange?logo=autodesk&logoColor=white)
![License](https://img.shields.io/badge/License-Internal-lightgrey)

A Python Flask web application that automates the transfer of Issues between Autodesk Forma (ACC) projects using the Autodesk Issues API. Users authenticate via Autodesk OAuth, select issues from a source project, and recreate them in a destination project — including comments, attachments, and photo references — through a simple web interface.

---

## Features

- **Autodesk OAuth Authentication** — Secure 3-legged OAuth login flow with token caching
- **Issue Transfer** — Copy selected issues between Forma projects with full metadata
- **Intelligent Type/Subtype Mapping** — Semantic synonym fallback (e.g. `observation` → `non-conformance`) when source issue types are inactive or missing in the destination project
- **Photo Reference Transfer** — Discovers linked photos via the BIM360 Relationships API and re-attaches them as files on the destination issue
- **Optional Comment Transfer** — Copy issue comments to the destination issue
- **Optional Attachment Transfer** — Download and re-upload file attachments to the destination project via Autodesk OSS
- **Permission Validation** — Pre-flight check confirms the authenticated user can create issues in the destination project before any transfer begins
- **Assignee Fallback** — If an assignee does not exist in the destination project, the issue is still created without one rather than failing the transfer
- **Project Selection UI** — Select source and destination projects through a web interface

---

## Workflow

| Step | Description |
|------|-------------|
| 1. Login | Authenticate with Autodesk OAuth |
| 2. Select Projects | Choose source and destination Forma projects |
| 3. Select Issues | Pick one or more issues to transfer |
| 4. Transfer | Map supported fields and recreate issues in destination |
| 5. Result Summary | Review transferred issues and per-issue transfer status |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python, Flask 3.1 |
| Authentication | Autodesk OAuth 2.0 (3-legged) |
| APIs | Autodesk Forma / ACC Issues API, BIM360 Relationships API, Autodesk OSS |
| Frontend | HTML, CSS, JavaScript |

---

## Prerequisites

- Python 3.9 or higher
- An [Autodesk Platform Services (APS)](https://aps.autodesk.com/) application with the following OAuth scopes approved:
  ```
  data:read  data:write  account:read  account:write
  ```
- An Autodesk Forma / ACC account with access to at least one project containing issues
- Issue creation permissions on the destination project

---

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd <repo-name>
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the root directory:

```env
# Required — Autodesk APS application credentials
CLIENT_ID=YOUR_AUTODESK_CLIENT_ID
CLIENT_SECRET=YOUR_AUTODESK_CLIENT_SECRET

# OAuth callback URL (must match your APS app's registered callback)
CALLBACK_URL=http://localhost:8080/api/auth/callback

# Flask session secret key
FLASK_SESSION_KEY=YOUR_SECRET_KEY

# Autodesk region: US or EMEA
REGION=US

# Optional — default project IDs pre-populated in the UI (b. prefix required)
SOURCE_PROJECT_ID=b.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
DEST_PROJECT_ID=b.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> **Important:** Never commit your `.env` file or Autodesk credentials to Git. The `.env` file is included in `.gitignore`.

### 4. Run the application

```bash
python acc_oauth/app.py
```

The application will start a local Flask server. Open your browser to `http://localhost:5000` and authenticate with your Autodesk account when prompted.

---

## API Routes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serves the main web UI |
| GET | `/api/auth` | Triggers the Autodesk OAuth login flow |
| GET | `/api/status` | Returns auth state, region, and configuration defaults |
| POST | `/api/resolve-projects` | Parses project IDs from URLs, raw UUIDs, or `b.xxx` format |
| GET | `/api/projects` | Lists accessible projects using `.env` defaults |
| POST | `/api/transfer` | Main transfer endpoint — clones a batch of issues with options |
| GET | `/api/debug-project-list` | Diagnostic endpoint testing multiple project list APIs |

---

## Supported Transfer Data

| Field | Transferred |
|-------|-------------|
| Issue title | Yes |
| Description | Yes |
| Status | Yes (with legacy field normalization) |
| Issue type / subtype | Yes (with semantic fallback mapping) |
| Assignee | Yes (removed gracefully if not in destination) |
| Due dates | Yes |
| Comments | Optional |
| Attachments | Optional |
| Photo references | Yes (via BIM360 Relationships API) |
| Custom fields | No — see Limitations |
| Pushpin / placement coordinates | No — see Limitations |

---

## Key Limitations

### Model-Based Placement Not Supported
Issues linked to clash or model locations (pushpins) cannot be transferred through the API. Exact placement coordinates and coordination context will not carry over.

### Issue Thumbnails Not Replicated
Thumbnails are tied to model placement and cannot be recreated via the API. File attachments may be uploaded as a workaround but will not behave the same as native model-based thumbnails.

### Custom Fields Not Transferred
The Autodesk Issues API write endpoint does not currently support custom attribute fields. Only standard issue fields are cloned.

### Issue Cross-References Re-linked as Attachments
Native issue-to-issue links are not supported on write. Where applicable, references are re-attached as file attachments rather than native issue links.

### API Mapping Constraints
Some issue fields or subtypes may differ between projects depending on project configuration, permissions, and which issue types are active in the destination.

---

## Project Structure

```
Issues_Transfer_Forma_Workflow/
├── acc_oauth/
│   ├── app.py              # Main Flask application and all API logic
│   ├── templates/
│   │   └── index.html      # Single-page web UI
│   └── .env                # Environment variables (gitignored)
├── requirements.txt        # Python dependencies
├── .gitignore
└── README.md
```

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | 3.1.2 | Web framework |
| Flask-Cors | 4.0.0 | CORS support |
| requests | 2.32.5 | HTTP client for Autodesk APIs |
| python-dotenv | 1.0.0 | `.env` file loading |
| acc_sdk | latest | Autodesk ACC SDK |

---

## References

- [Autodesk Platform Services — Issues API](https://aps.autodesk.com/en/docs/acc/v1/overview/field-guide/issues/)
- [Autodesk Platform Services — Authentication (OAuth 2.0)](https://aps.autodesk.com/en/docs/oauth/v2/developers_guide/overview/)
- [Autodesk Platform Services — Data Management (OSS)](https://aps.autodesk.com/en/docs/data/v2/developers_guide/overview/)
- [BIM360 Relationships API](https://aps.autodesk.com/en/docs/bim360/v1/reference/http/relationships/)

---

## Developed By

Port of Seattle — Design Technology Team - Ashvath Premkumar. Built to streamline issue migration and coordination workflows across Autodesk Forma projects.

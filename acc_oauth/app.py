import os
import re
import time
import uuid
import requests
import webbrowser
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# Config
# --------------------------------------------------
CLIENT_ID         = os.getenv("CLIENT_ID")
CLIENT_SECRET     = os.getenv("CLIENT_SECRET")
CALLBACK_URL      = os.getenv("CALLBACK_URL", "http://localhost:8080/")
REGION            = os.getenv("REGION", "US")
FLASK_SESSION_KEY = os.getenv("FLASK_SESSION_KEY", "dev-secret-key")
DEFAULT_HUB_ID    = os.getenv("HUB_ID")

DEFAULT_SOURCE_PROJECT_ID = os.getenv("SOURCE_PROJECT_ID", "")
DEFAULT_DEST_PROJECT_ID   = os.getenv("DEST_PROJECT_ID",   "")

SCOPES = [
    "data:read",
    "data:write",
    "account:read",
    "account:write",
]

app = Flask(__name__)
app.secret_key = FLASK_SESSION_KEY

# --------------------------------------------------
# OAuth
# --------------------------------------------------
auth_code    = None
cached_token = None


class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        if "/?code=" in self.path:
            match = re.search(r"code=([^&]+)", self.path)
            if match:
                auth_code = match.group(1)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authenticated successfully. You may close this tab.</h1>"
                )
                return
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b"<h1>Authentication failed.</h1>")

    def log_message(self, format, *args):
        return


def run_local_callback_server():
    server = HTTPServer(("localhost", 8080), AuthHandler)
    server.handle_request()


def get_user_token():
    global auth_code
    auth_code = None

    Thread(target=run_local_callback_server, daemon=True).start()

    scopes_str = " ".join(SCOPES)
    auth_url = (
        "https://developer.api.autodesk.com/authentication/v2/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={CALLBACK_URL}"
        f"&scope={scopes_str}"
    )
    webbrowser.open(auth_url)

    while auth_code is None:
        time.sleep(1)

    token_url = "https://developer.api.autodesk.com/authentication/v2/token"
    headers   = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          auth_code,
        "redirect_uri":  CALLBACK_URL,
    }
    response = requests.post(token_url, headers=headers, data=data, timeout=60)
    response.raise_for_status()
    return response.json()["access_token"]


def get_cached_token():
    global cached_token
    if not cached_token:
        cached_token = get_user_token()
    return cached_token


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def strip_b_prefix(value: str) -> str:
    if not value:
        return value
    return value[2:] if value.startswith("b.") else value


def auth_headers(token: str, content_type: str | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {token}",
        "x-ads-region":  REGION,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def extract_project_id(value: str) -> str:
    if not value:
        raise ValueError("Project value is empty.")
    value = value.strip()
    if re.fullmatch(r"b\.[0-9a-fA-F\-]{36}", value):
        return value
    if re.fullmatch(r"[0-9a-fA-F\-]{36}", value):
        return f"b.{value}"
    match = re.search(r"/projects/([0-9a-fA-F\-]{36})/", value)
    if match:
        return f"b.{match.group(1)}"
    raise ValueError(
        "Could not extract a valid project ID. "
        "Paste a full ACC project URL or a project ID."
    )


# --------------------------------------------------
# Issues API — read helpers
# --------------------------------------------------

def fetch_issues(token: str, project_id: str, status: str | None = None) -> list:
    prj_id = strip_b_prefix(project_id)
    url    = f"https://developer.api.autodesk.com/construction/issues/v1/projects/{prj_id}/issues"
    params = {"limit": 100, "offset": 0}
    if status:
        params["filter[status]"] = status

    all_issues = []
    while True:
        r = requests.get(url, headers=auth_headers(token), params=params, timeout=60)
        if not r.ok:
            raise Exception(f"Failed to fetch issues: {r.status_code} | {r.text}")
        payload = r.json()
        results = payload.get("results", [])
        all_issues.extend(results)
        if len(results) < params["limit"]:
            break
        params["offset"] += params["limit"]
    return all_issues


def fetch_issue_detail(token: str, project_id: str, issue_id: str) -> dict:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issues/{issue_id}"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        raise Exception(f"Failed to fetch issue {issue_id}: {r.status_code} | {r.text}")
    return r.json()


def fetch_issue_comments(token: str, project_id: str, issue_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issues/{issue_id}/comments"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        return []
    return r.json().get("results", [])


def fetch_issue_attachments(token: str, project_id: str, issue_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/attachments/{issue_id}/items"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        return []
    return r.json().get("attachments", [])


def fetch_project_issue_types(token: str, project_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issue-types"
    )
    r = requests.get(
        url,
        headers=auth_headers(token),
        params={"include": "subtypes"},
        timeout=60,
    )
    if not r.ok:
        print(f"[WARN] Could not fetch issue types for {prj_id}: {r.status_code}")
        return []
    return r.json().get("results", [])


def fetch_dest_attribute_definitions(token: str, project_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issue-attribute-definitions"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        return []
    return r.json().get("results", [])


def fetch_dest_attribute_mappings(token: str, project_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issue-attribute-mappings"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        return []
    return r.json().get("results", [])


def fetch_dest_root_cause_categories(token: str, project_id: str) -> list:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issue-root-cause-categories"
    )
    r = requests.get(
        url,
        headers=auth_headers(token),
        params={"include": "rootcauses"},
        timeout=60,
    )
    if not r.ok:
        return []
    return r.json().get("results", [])


# --------------------------------------------------
# Photo References — Relationships API + Photos API
# --------------------------------------------------

def fetch_issue_photo_references(token: str, project_id: str, issue_id: str) -> list:
    container_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/bim360/relationship/v2"
        f"/containers/{container_id}/relationships:intersect"
    )
    body = {
        "entities": [
            {
                "id":     issue_id,
                "type":   "issue",
                "domain": "autodesk-bim360-issue",
            }
        ]
    }
    r = requests.post(
        url,
        headers=auth_headers(token, "application/json"),
        json=body,
        timeout=60,
    )
    if not r.ok:
        print(f"[PHOTO] Relationships API failed: {r.status_code} | {r.text}")
        return []

    response_data = r.json()
    items = response_data if isinstance(response_data, list) else [response_data]

    photo_ids: list = []
    for item in items:
        for rel in item.get("relationships", []):
            for entity in rel.get("entities", []):
                if (
                    entity.get("domain") == "autodesk-construction-photo"
                    and entity.get("type") == "photo"
                ):
                    pid = entity.get("id")
                    if pid:
                        photo_ids.append(pid)

    print(f"[PHOTO] Found {len(photo_ids)} photo reference(s) for issue {issue_id}")
    return photo_ids


def fetch_photo_signed_url(token: str, project_id: str, photo_id: str) -> dict | None:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/photos/v1"
        f"/projects/{prj_id}/photos/{photo_id}"
    )
    r = requests.get(
        url,
        headers=auth_headers(token),
        params={"include": "signedUrls"},
        timeout=60,
    )
    if not r.ok:
        print(f"[PHOTO] GET photo {photo_id} failed: {r.status_code} | {r.text}")
        return None

    data     = r.json()
    signed   = data.get("signedUrls", {})
    file_url = signed.get("fileUrl")
    if not file_url:
        print(f"[PHOTO] No signedUrls.fileUrl for photo {photo_id}")
        return None

    return {
        "file_url": file_url,
        "title":    data.get("title", photo_id),
        "taken_at": data.get("takenAt", ""),
    }


def download_photo_bytes(signed_url: str) -> bytes | None:
    try:
        r = requests.get(signed_url, timeout=120)
        if not r.ok:
            print(f"[PHOTO] S3 download failed: {r.status_code}")
            return None
        return r.content
    except Exception as e:
        print(f"[PHOTO] S3 download exception: {e}")
        return None


# --------------------------------------------------
# OSS upload pipeline
# Uses POST /data/v1/projects/{id}/storage which
# auto-assigns to the wip.dm.prod global bucket.
# Confirmed from inspecting existing storageUrns:
#   urn:adsk.objects:os.object:wip.dm.prod/{uuid}.ext
# No topFolders call or hub ID needed.
# --------------------------------------------------

def get_signed_s3_upload_url(token: str, bucket_key: str, object_key: str) -> dict | None:
    url = (
        f"https://developer.api.autodesk.com/oss/v2"
        f"/buckets/{bucket_key}/objects/{object_key}/signeds3upload"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        print(f"[ATT] signeds3upload GET failed: {r.status_code} | {r.text}")
        return None
    return r.json()


def complete_s3_upload(token: str, bucket_key: str,
                       object_key: str, upload_key: str) -> bool:
    url = (
        f"https://developer.api.autodesk.com/oss/v2"
        f"/buckets/{bucket_key}/objects/{object_key}/signeds3upload"
    )
    r = requests.post(
        url,
        headers=auth_headers(token, "application/json"),
        json={"uploadKey": upload_key},
        timeout=60,
    )
    if not r.ok:
        print(f"[ATT] signeds3upload complete failed: {r.status_code} | {r.text}")
        return False
    return True


def download_attachment_bytes(token: str, storage_urn: str) -> bytes | None:
    prefix = "urn:adsk.objects:os.object:"
    if not storage_urn.startswith(prefix):
        print(f"[WARN] Unexpected storageUrn format: {storage_urn}")
        return None

    remainder = storage_urn[len(prefix):]
    slash_idx = remainder.find("/")
    if slash_idx == -1:
        return None

    bucket_key = remainder[:slash_idx]
    object_key = remainder[slash_idx + 1:]

    signed_url = (
        f"https://developer.api.autodesk.com/oss/v2"
        f"/buckets/{bucket_key}/objects/{object_key}/signeds3download"
    )
    r = requests.get(signed_url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        print(f"[WARN] signeds3download failed: {r.status_code} | {r.text}")
        return None

    download_url = r.json().get("url")
    if not download_url:
        return None

    resp = requests.get(download_url, timeout=120)
    if not resp.ok:
        print(f"[WARN] S3 download failed: {resp.status_code}")
        return None

    return resp.content


def upload_attachment_to_dest(
    token: str,
    dest_project_id: str,
    hub_id: str,
    display_name: str,
    file_name: str,
    file_bytes: bytes,
) -> str | None:
    """
    Uploads file bytes to dest project OSS.
    ACC requires a folder relationship in POST /storage.
    We construct the root folder URN directly from the project ID —
    format: urn:adsk.wipprod:fs.folder:co.{raw_project_uuid}
    This avoids the topFolders API call entirely.
    """
    prj_id      = dest_project_id   # with b. prefix
    raw_id      = strip_b_prefix(prj_id)
    ext         = os.path.splitext(file_name)[1] or ".bin"
    new_obj_key = f"{uuid.uuid4()}{ext}"

    # Construct root folder URN directly — no API call needed
    # ACC root folder URN pattern: urn:adsk.wipprod:fs.folder:co.{project-uuid}
    root_folder_urn = f"urn:adsk.wipprod:fs.folder:co.{raw_id}"

    storage_url = f"https://developer.api.autodesk.com/data/v1/projects/{prj_id}/storage"
    body = {
        "jsonapi": {"version": "1.0"},
        "data": {
            "type":       "objects",
            "attributes": {"name": new_obj_key},
            "relationships": {
                "target": {
                    "data": {
                        "type": "folders",
                        "id":   root_folder_urn,
                    }
                }
            },
        },
    }
    r = requests.post(
        storage_url,
        headers=auth_headers(token, "application/vnd.api+json"),
        json=body,
        timeout=60,
    )

    # If constructed URN fails, retry without folder relationship
    if not r.ok:
        print(f"[ATT] storage with folder URN failed ({r.status_code}), retrying without folder...")
        body["data"].pop("relationships", None)
        r = requests.post(
            storage_url,
            headers=auth_headers(token, "application/vnd.api+json"),
            json=body,
            timeout=60,
        )

    if not r.ok:
        print(f"[ATT] POST storage failed: {r.status_code} | {r.text}")
        return None

    storage_urn = r.json().get("data", {}).get("id")
    if not storage_urn:
        print(f"[ATT] No storage URN returned: {r.json()}")
        return None

    print(f"[ATT] Storage created: {storage_urn}")

    # Parse bucket_key / object_key from storageUrn
    prefix    = "urn:adsk.objects:os.object:"
    remainder = storage_urn[len(prefix):]
    slash_idx = remainder.find("/")
    if slash_idx == -1:
        print(f"[ATT] Cannot parse storageUrn: {storage_urn}")
        return None
    bucket_key     = remainder[:slash_idx]
    object_key_enc = remainder[slash_idx + 1:]

    # Get signed S3 upload URL
    signed = get_signed_s3_upload_url(token, bucket_key, object_key_enc)
    if not signed or not signed.get("urls"):
        return None

    upload_url = signed["urls"][0]
    upload_key = signed["uploadKey"]

    # PUT bytes to S3 (NO Authorization header)
    put_r = requests.put(upload_url, data=file_bytes, timeout=120)
    if not put_r.ok:
        print(f"[ATT] S3 PUT failed: {put_r.status_code}")
        return None

    # Finalize
    if not complete_s3_upload(token, bucket_key, object_key_enc, upload_key):
        return None

    return storage_urn

# --------------------------------------------------
# Hub ID — no longer needed for uploads.
# Returns a constant so downstream checks pass.
# --------------------------------------------------

def get_hub_id_for_project(token: str, project_id: str) -> str | None:
    print("[SETUP] Hub ID: using wip.dm.prod bucket directly — no hub lookup needed.")
    return "wip.dm.prod"


# --------------------------------------------------
# Issues API — write helpers
# --------------------------------------------------

def create_issue(token: str, project_id: str, payload: dict) -> dict:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issues"
    )
    r = requests.post(
        url,
        headers=auth_headers(token, "application/json"),
        json=payload,
        timeout=60,
    )
    if not r.ok:
        raise Exception(f"Failed to create issue: {r.status_code} | {r.text}")
    return r.json()


def post_comment(token: str, project_id: str, issue_id: str, body: str) -> None:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/issues/{issue_id}/comments"
    )
    requests.post(
        url,
        headers=auth_headers(token, "application/json"),
        json={"body": body},
        timeout=60,
    )


def post_attachments(token: str, project_id: str, new_issue_id: str,
                     attachments: list) -> None:
    if not attachments:
        return
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/attachments"
    )
    # Post one at a time — batch posting causes 409 conflicts
    for att in attachments:
        body = {"domainEntityId": new_issue_id, "attachments": [att]}
        r = requests.post(
            url,
            headers=auth_headers(token, "application/json"),
            json=body,
            timeout=60,
        )
        if not r.ok:
            print(f"[WARN] post_attachments failed for '{att.get('displayName')}': "
                  f"{r.status_code} | {r.text}")
        else:
            print(f"[ATT] Linked attachment: {att.get('displayName')}")


# --------------------------------------------------
# Attachment transfer pipeline
# --------------------------------------------------

def transfer_attachments(
    token: str,
    source_project_id: str,
    dest_project_id: str,
    source_issue_id: str,
    dest_issue_id: str,
    hub_id: str,
) -> dict:
    src_attachments = fetch_issue_attachments(token, source_project_id, source_issue_id)
    if not src_attachments:
        return {"attempted": 0, "succeeded": 0, "failed": 0}

    attempted  = len(src_attachments)
    succeeded  = 0
    dest_batch = []

    for att in src_attachments:
        storage_urn  = att.get("storageUrn")
        display_name = att.get("displayName", "attachment")
        file_name    = att.get("fileName",    "attachment")

        if not storage_urn:
            continue

        file_bytes = download_attachment_bytes(token, storage_urn)
        if not file_bytes:
            print(f"[WARN] Could not download attachment '{display_name}' — skipping.")
            continue

        new_storage_urn = upload_attachment_to_dest(
            token           = token,
            dest_project_id = dest_project_id,
            hub_id          = hub_id,
            display_name    = display_name,
            file_name       = file_name,
            file_bytes      = file_bytes,
        )
        if not new_storage_urn:
            print(f"[WARN] Upload failed for '{display_name}' — skipping.")
            continue

        ext_part    = os.path.splitext(file_name)[1]
        new_att_uid = str(uuid.uuid4())

        dest_batch.append({
            "attachmentId":   new_att_uid,
            "displayName":    display_name,
            "fileName":       f"{new_att_uid}{ext_part}",
            "attachmentType": att.get("attachmentType", "issue-attachment"),
            "storageUrn":     new_storage_urn,
        })
        succeeded += 1

        if len(dest_batch) == 100:
            post_attachments(token, dest_project_id, dest_issue_id, dest_batch)
            dest_batch = []

    if dest_batch:
        post_attachments(token, dest_project_id, dest_issue_id, dest_batch)

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed":    attempted - succeeded,
    }


# --------------------------------------------------
# Photo reference transfer pipeline
# --------------------------------------------------

def transfer_photo_references(
    token: str,
    source_project_id: str,
    dest_project_id: str,
    source_issue_id: str,
    dest_issue_id: str,
    hub_id: str,
) -> dict:
    photo_ids = fetch_issue_photo_references(token, source_project_id, source_issue_id)
    if not photo_ids:
        return {"attempted": 0, "succeeded": 0, "failed": 0}

    attempted  = len(photo_ids)
    succeeded  = 0
    dest_batch: list = []

    for photo_id in photo_ids:
        photo_info = fetch_photo_signed_url(token, source_project_id, photo_id)
        if not photo_info:
            print(f"[PHOTO] Could not get signed URL for photo {photo_id} — skipping.")
            continue

        file_bytes = download_photo_bytes(photo_info["file_url"])
        if not file_bytes:
            print(f"[PHOTO] Could not download bytes for photo {photo_id} — skipping.")
            continue

        safe_title   = re.sub(r"[^a-zA-Z0-9_\-]", "_", photo_info["title"])[:40]
        taken_str    = photo_info["taken_at"][:10] if photo_info["taken_at"] else "unknown"
        file_name    = f"ref_photo_{safe_title}_{taken_str}.jpg"
        display_name = f"{photo_info['title']} (ref photo {taken_str})"

        new_storage_urn = upload_attachment_to_dest(
            token           = token,
            dest_project_id = dest_project_id,
            hub_id          = hub_id,
            display_name    = display_name,
            file_name       = file_name,
            file_bytes      = file_bytes,
        )
        if not new_storage_urn:
            print(f"[PHOTO] Upload failed for photo {photo_id} — skipping.")
            continue

        new_att_uid = str(uuid.uuid4())
        dest_batch.append({
            "attachmentId":   new_att_uid,
            "displayName":    display_name,
            "fileName":       file_name,
            "attachmentType": "issue-attachment",
            "storageUrn":     new_storage_urn,
        })
        succeeded += 1

        if len(dest_batch) == 100:
            post_attachments(token, dest_project_id, dest_issue_id, dest_batch)
            dest_batch = []

    if dest_batch:
        post_attachments(token, dest_project_id, dest_issue_id, dest_batch)

    print(f"[PHOTO] Transfer complete — attempted={attempted} succeeded={succeeded} "
          f"failed={attempted - succeeded}")
    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed":    attempted - succeeded,
    }


# --------------------------------------------------
# Permissions
# --------------------------------------------------

def verify_user_permissions(token: str, project_id: str) -> dict:
    prj_id = strip_b_prefix(project_id)
    url = (
        f"https://developer.api.autodesk.com/construction/issues/v1"
        f"/projects/{prj_id}/users/me"
    )
    r = requests.get(url, headers=auth_headers(token), timeout=60)
    if not r.ok:
        return {"_error": f"{r.status_code} | {r.text}"}
    return r.json()


# --------------------------------------------------
# Field mapping helpers
# --------------------------------------------------

VALID_STATUSES = {"draft", "open", "answered", "closed"}


def map_status(source_status: str | None) -> str:
    if not source_status:
        return "open"
    s = source_status.lower()
    if s in VALID_STATUSES:
        return s
    legacy = {
        "in_review": "answered", "in review": "answered",
        "not_started": "open", "void": "closed", "work_completed": "closed",
    }
    return legacy.get(s, "open")


def extract_watcher_ids(source_issue: dict) -> list[str]:
    raw = source_issue.get("watchers") or []
    ids = []
    for w in raw:
        if isinstance(w, str) and w:
            ids.append(w)
        elif isinstance(w, dict):
            wid = w.get("id") or w.get("userId")
            if wid:
                ids.append(wid)
    return ids


TYPE_SYNONYMS: dict[str, str] = {
    "observation":    "non-conformance",
    "collision":      "coordination",
    "collission":     "coordination",
    "clash":          "coordination",
    "general":        "design",
    "punch list":     "quality",
    "pre-punch list": "quality",
    "work list":      "design",
    "commissioning":  "quality",
    "warranty":       "quality",
    "potential rfi":  "design",
    "rfi":            "design",
}

SUBTYPE_SYNONYMS: dict[tuple[str, str], str] = {
    ("observation", "observation"):   "non-conformance",
    ("observation", "quality"):       "quality",
    ("observation", "safety"):        "safety",
    ("coordination", "clash"):        "clash",
    ("coordination", "coordination"): "coordination",
}


def build_source_subtype_lookup(source_types: list) -> dict:
    lookup: dict = {}
    for t in source_types:
        type_title = (t.get("title") or "").strip()
        for s in t.get("subtypes", []):
            sub_id    = s.get("id")
            sub_title = (s.get("title") or "").strip()
            if sub_id:
                lookup[sub_id] = {"typeTitle": type_title, "subtypeTitle": sub_title}
    return lookup


def build_dest_type_lookup(dest_types: list) -> dict:
    by_uuid:  dict = {}
    by_title: dict = {}
    active_types_summary: list = []
    default_subtype_id: str | None = None

    for t in dest_types:
        if not t.get("isActive", False):
            continue

        type_title = (t.get("title") or "").lower().strip()
        type_id    = t.get("id")
        subs_by_title: dict = {}
        first_sub_id: str | None = None
        active_sub_titles: list = []

        for s in t.get("subtypes", []):
            if not s.get("isActive", False):
                continue
            sub_title = (s.get("title") or "").lower().strip()
            sub_id    = s.get("id")
            if sub_id:
                subs_by_title[sub_title] = sub_id
                by_uuid[sub_id] = {"typeId": type_id, "subtypeId": sub_id}
                active_sub_titles.append(sub_title)
                if first_sub_id is None:
                    first_sub_id = sub_id
                if default_subtype_id is None:
                    default_subtype_id = sub_id

        if first_sub_id:
            by_title[type_title] = {
                "typeId":            type_id,
                "subtypes_by_title": subs_by_title,
                "first_subtype_id":  first_sub_id,
            }
            active_types_summary.append({
                "typeTitle": t.get("title"),
                "subtypes":  active_sub_titles,
            })

    return {
        "by_uuid":              by_uuid,
        "by_title":             by_title,
        "default_subtype_id":   default_subtype_id,
        "active_types_summary": active_types_summary,
    }


def smart_semantic_fallback(
    src_type_title: str,
    src_sub_title:  str,
    by_title:       dict,
) -> tuple[str | None, str | None, str]:
    sub_synonym_key = (src_type_title.lower(), src_sub_title.lower())
    dest_sub_title  = SUBTYPE_SYNONYMS.get(sub_synonym_key)
    if dest_sub_title:
        for t_title, t_data in by_title.items():
            sub_id = t_data["subtypes_by_title"].get(dest_sub_title)
            if sub_id:
                return (
                    t_data["typeId"], sub_id,
                    f"semantic_subtype({src_type_title}>{src_sub_title} → {t_title}>{dest_sub_title})"
                )

    dest_type_title = TYPE_SYNONYMS.get(src_type_title.lower())
    if dest_type_title:
        matched = by_title.get(dest_type_title)
        if matched:
            return (
                matched["typeId"], matched["first_subtype_id"],
                f"semantic_type({src_type_title} → {dest_type_title})"
            )

    return None, None, "no_semantic_match"


def resolve_type_ids_for_issue(
    source_issue:          dict,
    dest_lookup:           dict,
    source_subtype_lookup: dict,
) -> tuple[str | None, str | None, str]:
    by_uuid  = dest_lookup.get("by_uuid",  {})
    by_title = dest_lookup.get("by_title", {})

    src_subtype_id = source_issue.get("issueSubtypeId")

    if src_subtype_id and src_subtype_id in by_uuid:
        m = by_uuid[src_subtype_id]
        return m["typeId"], m["subtypeId"], "uuid_direct"

    src_type_title = ""
    src_sub_title  = ""
    if src_subtype_id and source_subtype_lookup:
        src_titles     = source_subtype_lookup.get(src_subtype_id, {})
        src_type_title = src_titles.get("typeTitle", "").lower().strip()
        src_sub_title  = src_titles.get("subtypeTitle", "").lower().strip()

    if src_type_title:
        matched_type = by_title.get(src_type_title)
        if matched_type:
            subtype_id = matched_type["subtypes_by_title"].get(src_sub_title)
            if subtype_id:
                return matched_type["typeId"], subtype_id, "title_exact"

            first_sub = matched_type.get("first_subtype_id")
            if first_sub:
                return (
                    matched_type["typeId"], first_sub,
                    f"title_type_only(src_sub='{src_sub_title}')"
                )

    if src_type_title:
        t_id, s_id, method = smart_semantic_fallback(
            src_type_title, src_sub_title, by_title
        )
        if s_id:
            return t_id, s_id, method

    default = dest_lookup.get("default_subtype_id")
    if default:
        label = (
            f"absolute_default("
            f"src='{src_type_title or 'unknown'}>{src_sub_title or 'unknown'}')"
        )
        return None, default, label

    return None, None, "no_match"


# --------------------------------------------------
# Core clone logic
# --------------------------------------------------

def clone_issue(
    token: str,
    source_project_id: str,
    dest_project_id: str,
    source_issue_id: str,
    dest_type_lookup: dict,
    source_subtype_lookup: dict,
    hub_id: str,
    copy_comments: bool    = True,
    copy_attachments: bool = True,
    copy_photo_refs: bool  = True,
) -> dict:

    source = fetch_issue_detail(token, source_project_id, source_issue_id)

    type_id, subtype_id, match_method = resolve_type_ids_for_issue(
        source_issue          = source,
        dest_lookup           = dest_type_lookup,
        source_subtype_lookup = source_subtype_lookup,
    )

    print(
        f"[TYPE] '{source.get('title')}' | src_subtype={source.get('issueSubtypeId')} "
        f"→ dest_subtype={subtype_id} [{match_method}]"
    )

    not_cloned = [
        "placement (pushpin) — API read-only",
        "thumbnail — tied to placement",
        "references — re-linked as attachments (see attachments section)",
        "custom fields — Issues API write not supported",
    ]

    if not subtype_id:
        raise Exception(
            f"Cannot clone '{source.get('title')}': no active issueSubtypeId "
            "found in destination project. Add at least one active issue type."
        )

    used_fallback = "semantic" in match_method or "default" in match_method
    if used_fallback:
        not_cloned.append(
            f"type/subtype — redirected via {match_method} "
            "(source type inactive or absent in destination)"
        )

    new_payload: dict = {
        "title":          source.get("title") or "Untitled",
        "description":    source.get("description") or "",
        "status":         map_status(source.get("status")),
        "issueSubtypeId": subtype_id,
        "published":      False,
    }

    for date_field in ("startDate", "dueDate"):
        val = source.get(date_field)
        if val:
            new_payload[date_field] = val

    assignee_id     = source.get("assignedTo")
    assignee_cloned = False
    if assignee_id:
        new_payload["assignedTo"]     = assignee_id
        new_payload["assignedToType"] = source.get("assignedToType") or "user"

    watcher_ids = extract_watcher_ids(source)
    if watcher_ids:
        new_payload["watchers"] = watcher_ids

    for src_field, dest_field in [
        ("locationDetails", "locationDetails"),
        ("rootCauseId",     "rootCauseId"),
    ]:
        val = source.get(src_field)
        if val:
            new_payload[dest_field] = val

    try:
        new_issue = create_issue(token, dest_project_id, new_payload)
        if assignee_id:
            assignee_cloned = True
    except Exception as e:
        err_str = str(e)
        if assignee_id and ("403" in err_str or "422" in err_str or "assignee" in err_str.lower()):
            new_payload.pop("assignedTo",     None)
            new_payload.pop("assignedToType", None)
            new_issue = create_issue(token, dest_project_id, new_payload)
            not_cloned.append(
                f"assignee ({assignee_id}) — cross-company or not in dest project"
            )
        else:
            raise

    new_issue_id = new_issue.get("id")
    if not new_issue_id:
        raise Exception(f"Issue created but no ID returned: {new_issue}")

    # Copy comments
    if copy_comments:
        comments = fetch_issue_comments(token, source_project_id, source_issue_id)
        for c in comments:
            body = c.get("body") or ""
            if body:
                prefix = f"[Transferred — originally by {c.get('createdBy', 'unknown')}]\n"
                post_comment(token, dest_project_id, new_issue_id, prefix + body)

    # Copy standard file attachments
    att_summary = {"attempted": 0, "succeeded": 0, "failed": 0}
    if copy_attachments:
        att_summary = transfer_attachments(
            token             = token,
            source_project_id = source_project_id,
            dest_project_id   = dest_project_id,
            source_issue_id   = source_issue_id,
            dest_issue_id     = new_issue_id,
            hub_id            = hub_id,
        )
        if att_summary["failed"] > 0:
            not_cloned.append(
                f"{att_summary['failed']} attachment(s) failed to re-upload "
                f"({att_summary['succeeded']} succeeded)"
            )

    # Copy photo references as attachments
    photo_ref_summary = {"attempted": 0, "succeeded": 0, "failed": 0}
    if copy_attachments and copy_photo_refs:
        photo_ref_summary = transfer_photo_references(
            token             = token,
            source_project_id = source_project_id,
            dest_project_id   = dest_project_id,
            source_issue_id   = source_issue_id,
            dest_issue_id     = new_issue_id,
            hub_id            = hub_id,
        )
        if photo_ref_summary["failed"] > 0:
            not_cloned.append(
                f"{photo_ref_summary['failed']} reference photo(s) failed to transfer "
                f"({photo_ref_summary['succeeded']} succeeded)"
            )

    return {
        "sourceIssueId":      source_issue_id,
        "destIssueId":        new_issue_id,
        "title":              source.get("title"),
        "matchMethod":        match_method,
        "usedFallback":       used_fallback,
        "typeMatched":        match_method in ("uuid_direct", "title_exact"),
        "assigneeCloned":     assignee_cloned,
        "attachmentsSummary": att_summary,
        "photoRefSummary":    photo_ref_summary,
        "notCloned":          not_cloned,
    }


def clone_issues_batch(
    token: str,
    source_project_id: str,
    dest_project_id: str,
    issue_ids: list[str],
    copy_comments: bool    = True,
    copy_attachments: bool = True,
    copy_photo_refs: bool  = True,
) -> dict:

    me             = verify_user_permissions(token, dest_project_id)
    perm_error     = me.get("_error")
    permissions_ok = True

    if perm_error:
        permissions_ok = False
        print(f"[WARN] Permission check failed: {perm_error}. Proceeding anyway.")
    else:
        permitted = me.get("issues", {}).get("permittedActions", [])
        if permitted and "new" not in permitted:
            raise Exception(
                "User does not have permission to create issues in the destination. "
                f"Permitted: {permitted}"
            )

    print("[SETUP] Fetching source project issue types...")
    source_types          = fetch_project_issue_types(token, source_project_id)
    source_subtype_lookup = build_source_subtype_lookup(source_types)
    print(f"[SETUP] Source: {len(source_subtype_lookup)} subtype(s) indexed.")

    print("[SETUP] Fetching destination project issue types...")
    dest_types       = fetch_project_issue_types(token, dest_project_id)
    dest_type_lookup = build_dest_type_lookup(dest_types)

    active_summary = dest_type_lookup.get("active_types_summary", [])
    print(f"[SETUP] Destination active types: "
          f"{[t['typeTitle'] for t in active_summary]}")
    for t in active_summary:
        print(f"  {t['typeTitle']}: active subtypes = {t['subtypes']}")

    hub_id = get_hub_id_for_project(token, dest_project_id)

    results: list = []
    errors:  list = []

    for issue_id in issue_ids:
        try:
            result = clone_issue(
                token                 = token,
                source_project_id     = source_project_id,
                dest_project_id       = dest_project_id,
                source_issue_id       = issue_id,
                dest_type_lookup      = dest_type_lookup,
                source_subtype_lookup = source_subtype_lookup,
                hub_id                = hub_id or "",
                copy_comments         = copy_comments,
                copy_attachments      = copy_attachments,
                copy_photo_refs       = copy_photo_refs,
            )
            results.append(result)
        except Exception as e:
            errors.append({"issueId": issue_id, "error": str(e)})

    exact_matches    = sum(1 for r in results if r["matchMethod"] in ("uuid_direct", "title_exact"))
    fallback_matches = sum(1 for r in results if r["usedFallback"])
    partial_matches  = len(results) - exact_matches - fallback_matches

    total_att_attempted = sum(r.get("attachmentsSummary", {}).get("attempted", 0) for r in results)
    total_att_succeeded = sum(r.get("attachmentsSummary", {}).get("succeeded", 0) for r in results)
    total_att_failed    = sum(r.get("attachmentsSummary", {}).get("failed",    0) for r in results)

    total_photo_attempted = sum(r.get("photoRefSummary", {}).get("attempted", 0) for r in results)
    total_photo_succeeded = sum(r.get("photoRefSummary", {}).get("succeeded", 0) for r in results)
    total_photo_failed    = sum(r.get("photoRefSummary", {}).get("failed",    0) for r in results)

    return {
        "transferred":         len(results),
        "failed":              len(errors),
        "results":             results,
        "errors":              errors,
        "permissionsVerified": permissions_ok,
        "typeMatchSummary": {
            "exact":    exact_matches,
            "partial":  partial_matches,
            "fallback": fallback_matches,
        },
        "attachmentSummary": {
            "attempted": total_att_attempted,
            "succeeded": total_att_succeeded,
            "failed":    total_att_failed,
        },
        "photoRefSummary": {
            "attempted": total_photo_attempted,
            "succeeded": total_photo_succeeded,
            "failed":    total_photo_failed,
        },
        "activeDestTypes": active_summary,
    }


# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({
        "running":                True,
        "authenticated":          cached_token is not None,
        "defaultSourceProjectId": DEFAULT_SOURCE_PROJECT_ID,
        "defaultDestProjectId":   DEFAULT_DEST_PROJECT_ID,
        "region":                 REGION,
    })


@app.route("/api/auth", methods=["GET"])
def api_auth():
    try:
        global cached_token
        cached_token = get_user_token()
        return jsonify({"status": "authenticated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resolve-projects", methods=["POST"])
def api_resolve_projects():
    try:
        body     = request.get_json(silent=True) or {}
        src_raw  = body.get("source",      "")
        dest_raw = body.get("destination", "")
        src_pid  = extract_project_id(src_raw)  if src_raw  else ""
        dest_pid = extract_project_id(dest_raw) if dest_raw else ""
        return jsonify({"sourceProjectId": src_pid, "destProjectId": dest_pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/projects", methods=["GET"])
def api_list_projects():
    """
    Builds a project list from the .env defaults + any project the user
    can reach via the Issues API. Since the Data Management hub endpoint
    returns 403 for this account, we use a known-working alternative:
    fetch the two configured projects and return them as selectable options.
    Users can still paste any URL/ID into the inputs directly.
    """
    try:
        token = get_cached_token()

        # Collect projects to probe — start with .env defaults
        candidate_ids = []
        if DEFAULT_SOURCE_PROJECT_ID:
            candidate_ids.append(DEFAULT_SOURCE_PROJECT_ID)
        if DEFAULT_DEST_PROJECT_ID and DEFAULT_DEST_PROJECT_ID not in candidate_ids:
            candidate_ids.append(DEFAULT_DEST_PROJECT_ID)

        all_projects = []
        for pid in candidate_ids:
            prj_id = strip_b_prefix(pid)
            # Fetch one issue just to confirm access and get the project name
            # via the issue types endpoint which returns the project container
            types_url = (
                f"https://developer.api.autodesk.com/construction/issues/v1"
                f"/projects/{prj_id}/issue-types"
            )
            r = requests.get(
                types_url,
                headers=auth_headers(token),
                params={"include": "subtypes"},
                timeout=30,
            )
            if r.ok:
                # Use the issues users/me endpoint to get any project name info
                # Fall back to using the project ID as display name
                me_url = (
                    f"https://developer.api.autodesk.com/construction/issues/v1"
                    f"/projects/{prj_id}/users/me"
                )
                me_r = requests.get(me_url, headers=auth_headers(token), timeout=30)
                name = f"Project {pid}"
                if me_r.ok:
                    me_data = me_r.json()
                    # Try to get a display name from available fields
                    name = (
                        me_data.get("projectName") or
                        me_data.get("name") or
                        f"Project {pid}"
                    )
                all_projects.append({"id": pid, "name": name})

        all_projects.sort(key=lambda p: p["name"].lower())
        return jsonify({"projects": all_projects, "count": len(all_projects)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/debug-project-list", methods=["GET"])
def debug_project_list():
    try:
        token = get_cached_token()

        # Try 1: ACC Admin v1 projects (uses account:read scope)
        r1 = requests.get(
            "https://developer.api.autodesk.com/construction/admin/v1/projects",
            headers=auth_headers(token),
            params={"limit": 5},
            timeout=30,
        )

        # Try 2: ACC Admin v2 projects
        r2 = requests.get(
            "https://developer.api.autodesk.com/construction/admin/v2/projects",
            headers=auth_headers(token),
            params={"limit": 5},
            timeout=30,
        )

        # Try 3: BIM360 account admin projects
        r3 = requests.get(
            "https://developer.api.autodesk.com/hq/v1/accounts",
            headers=auth_headers(token),
            timeout=30,
        )

        # Try 4: ACC Issues — list all containers (projects) the user has issue access to
        r4 = requests.get(
            "https://developer.api.autodesk.com/construction/issues/v1/projects",
            headers=auth_headers(token),
            timeout=30,
        )

        return jsonify({
            "admin_v1_status":   r1.status_code,
            "admin_v1_response": r1.text[:400],
            "admin_v2_status":   r2.status_code,
            "admin_v2_response": r2.text[:400],
            "hq_accounts_status":   r3.status_code,
            "hq_accounts_response": r3.text[:400],
            "issues_projects_status":   r4.status_code,
            "issues_projects_response": r4.text[:400],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/transfer", methods=["POST"])
def api_transfer():
    try:
        body = request.get_json(silent=True) or {}

        source_project_id = body.get("sourceProjectId") or DEFAULT_SOURCE_PROJECT_ID
        dest_project_id   = body.get("destProjectId")   or DEFAULT_DEST_PROJECT_ID
        issue_ids         = body.get("issueIds",         [])
        copy_comments     = body.get("copyComments",     True)
        copy_attachments  = body.get("copyAttachments",  True)
        copy_photo_refs   = body.get("copyPhotoRefs",    True)

        if not source_project_id or not dest_project_id:
            return jsonify({"error": "Both sourceProjectId and destProjectId are required."}), 400

        source_project_id = extract_project_id(source_project_id)
        dest_project_id   = extract_project_id(dest_project_id)

        token = get_cached_token()

        if not issue_ids:
            all_issues = fetch_issues(token, source_project_id)
            issue_ids  = [i["id"] for i in all_issues if i.get("id")]

        result = clone_issues_batch(
            token             = token,
            source_project_id = source_project_id,
            dest_project_id   = dest_project_id,
            issue_ids         = issue_ids,
            copy_comments     = copy_comments,
            copy_attachments  = copy_attachments,
            copy_photo_refs   = copy_photo_refs,
        )

        return jsonify({
            "status":              "success",
            "sourceProjectId":     source_project_id,
            "destProjectId":       dest_project_id,
            "transferred":         result["transferred"],
            "failed":              result["failed"],
            "errors":              result["errors"],
            "results":             result["results"],
            "copyComments":        copy_comments,
            "copyAttachments":     copy_attachments,
            "copyPhotoRefs":       copy_photo_refs,
            "permissionsVerified": result["permissionsVerified"],
            "typeMatchSummary":    result["typeMatchSummary"],
            "attachmentSummary":   result["attachmentSummary"],
            "photoRefSummary":     result["photoRefSummary"],
            "activeDestTypes":     result["activeDestTypes"],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# Main
# --------------------------------------------------
if __name__ == "__main__":
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in the .env file.")
    print("Forma Issues Transfer UI running at http://127.0.0.1:5001")
    app.run(debug=True, host="127.0.0.1", port=5001)
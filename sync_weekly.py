#!/usr/bin/env python3
"""Monitor ruanyf/weekly and push new issues to Feishu via Open API."""

import os
import re
import json
import sys
import time
import subprocess
import urllib.request
import urllib.error

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "")

WEEKLY_REPO = "https://github.com/ruanyf/weekly.git"
CLONE_DIR = "/tmp/weekly_repo"
STATE_FILE = "last_issue.txt"
MAX_CHUNK_LEN = 25000
MAX_RETRIES = 2
RETRY_DELAY = 2

# Feishu API endpoints
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"


def get_token():
    """Obtain a tenant_access_token using APP_ID and APP_SECRET."""
    data = json.dumps({
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }).encode("utf-8")

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
                if body.get("code") == 0:
                    return body["tenant_access_token"]
                print(f"Token error: code={body.get('code')} msg={body.get('msg')}", file=sys.stderr)
        except Exception as e:
            print(f"Token request failed: {e}", file=sys.stderr)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return None



def create_multipart_form(fields, file_field, file_name, file_data, content_type):
    """Build multipart/form-data body for Feishu image upload."""
    import uuid
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        body += f"{value}\r\n".encode("utf-8")
    body += f"--{boundary}\r\n".encode("utf-8")
    body += f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode("utf-8")
    body += f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    body += file_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return boundary, body


def upload_image_to_feishu(image_url, token):
    """Download image from URL, upload to Feishu, return image_key or None."""
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            image_data = resp.read()
        if len(image_data) > 10 * 1024 * 1024:
            print(f"Image too large, skip: {image_url[:80]}", file=sys.stderr)
            return None
        ct = resp.headers.get("Content-Type", "image/png")
        ext = image_url.rsplit(".", 1)[-1].lower().split("?")[0]
        if ext in ("jpg", "jpeg"): ct = "image/jpeg"
        elif ext == "png": ct = "image/png"
        elif ext == "gif": ct = "image/gif"
        elif ext == "webp": ct = "image/webp"
        fname = f"image.{ext if ext in ('jpg','jpeg','png','gif','webp') else 'png'}"
        boundary, body = create_multipart_form(
            {"image_type": "message"}, "image", fname, image_data, ct
        )
        req2 = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/images",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}"
            }
        )
        with urllib.request.urlopen(req2, timeout=30) as resp2:
            result = json.loads(resp2.read())
        if result.get("code") == 0:
            return result["data"]["image_key"]
        print(f"Feishu upload err: {result.get('msg')}", file=sys.stderr)
    except Exception as e:
        print(f"Image skip: {image_url[:80]} - {e}", file=sys.stderr)
    return None


def process_markdown_images(content, token):
    """Replace ![](url) with Feishu-compatible img_v3_{image_key} format."""
    images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
    print(f"Processing images... Found {len(images)} image(s) in issue", file=sys.stderr)
    pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def replace(m):
        alt = m.group(1)
        url = m.group(2)
        if url.startswith("img_") or url.startswith("img_v3_"):
            return m.group(0)
        key = upload_image_to_feishu(url, token)
        if key:
            return f"![{alt}](img_v3_{key})"
        return f"[图片: {alt or '无'}]"

    return pattern.sub(replace, content)


def clone_weekly():
    """Shallow-clone the weekly repo into CLONE_DIR."""
    if os.path.exists(CLONE_DIR):
        subprocess.run(["rm", "-rf", CLONE_DIR], check=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", WEEKLY_REPO, CLONE_DIR],
        check=True, capture_output=True, text=True
    )


def scan_issues():
    """Return sorted list of (issue_number, file_path) from cloned docs/."""
    docs_dir = os.path.join(CLONE_DIR, "docs")
    issues = []
    pattern = re.compile(r"^issue-(\d+)\.md$")
    for fname in os.listdir(docs_dir):
        m = pattern.match(fname)
        if m:
            issues.append((int(m.group(1)), os.path.join(docs_dir, fname)))
    issues.sort(key=lambda x: x[0])
    return issues


def read_state():
    """Read last pushed issue number from STATE_FILE. Returns 0 on first run."""
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE) as f:
        content = f.read().strip()
    return int(content) if content else 0


def write_state(num):
    """Write latest pushed issue number to STATE_FILE."""
    with open(STATE_FILE, "w") as f:
        f.write(str(num) + "\n")


def extract_title(content):
    """Extract the first H1 heading, stripping the standard weekly prefix."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            m = re.match(r"科技爱好者周刊[（(]\s*第\s*\d+\s*期\s*[）)][：:]\s*", title)
            if m:
                title = title[m.end():]
            return title
    return ""


def split_content(content, max_len=MAX_CHUNK_LEN):
    """Split content by paragraph boundaries into chunks under max_len."""
    if len(content) <= max_len:
        return [content]

    paragraphs = re.split(r"\n\n+", content)
    chunks = []
    buf = ""

    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > max_len:
            chunks.append(buf.strip())
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para

    if buf.strip():
        chunks.append(buf.strip())

    return chunks


def build_card(issue_num, title, chunk, part=None, total=None):
    """Build a Feishu interactive card payload."""
    header_title = f"第{issue_num}期：{title}"
    if total and total > 1:
        header_title += f" ({part}/{total})"

    elements = [
        {"tag": "markdown", "content": chunk},
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": (
                        f"原文链接：https://github.com/ruanyf/weekly/"
                        f"blob/master/docs/issue-{issue_num}.md"
                    )
                }
            ]
        }
    ]

    # Strip markdown images - Feishu cards need image_key for actual images
    chunk = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", chunk)

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": "blue"
        },
        "elements": elements
    }


def send_card(card, token):
    """POST an interactive card to the Feishu group chat. Retries on failure."""
    if not token or not CHAT_ID:
        return False

    payload = json.dumps({
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False)
    }).encode("utf-8")

    last_error = ""

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                SEND_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {token}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                if body.get("code") == 0:
                    return True
                last_error = f"code={body.get('code')} msg={body.get('msg')}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            last_error = f"HTTP {e.code}: {body}"
        except Exception as e:
            last_error = str(e)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    print(f"Push failed after {MAX_RETRIES} retries: {last_error}", file=sys.stderr)
    return False


def push_issue(issue_num, token):
    """Read issue markdown and push to Feishu, splitting if needed."""
    path = os.path.join(CLONE_DIR, "docs", f"issue-{issue_num}.md")
    if not os.path.exists(path):
        print(f"File not found: {path}", file=sys.stderr)
        return False

    with open(path, encoding="utf-8") as f:
        content = f.read()

    title = extract_title(content)
    chunks = split_content(content)
    total = len(chunks)
    ok = True

    for i, chunk in enumerate(chunks, 1):
        p = i if total > 1 else None
        t = total if total > 1 else None
        card = build_card(issue_num, title, chunk, p, t)
        if not send_card(card, token):
            ok = False

    return ok


def main():
    missing = []
    if not APP_ID:
        missing.append("FEISHU_APP_ID")
    if not APP_SECRET:
        missing.append("FEISHU_APP_SECRET")
    if not CHAT_ID:
        missing.append("FEISHU_CHAT_ID")
    if missing:
        print(f"FATAL: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    if not token:
        print("FATAL: could not obtain access token", file=sys.stderr)
        sys.exit(1)

    clone_weekly()
    issues = scan_issues()

    if not issues:
        print("No issue files found", file=sys.stderr)
        sys.exit(1)

    latest_num, _ = issues[-1]
    last_num = read_state()

    if last_num == 0:
        print(f"First run: recording latest issue #{latest_num} (no push)")
        write_state(latest_num)
        return

    if latest_num <= last_num:
        print(f"Up to date (latest={latest_num}, last_pushed={last_num})")
        return

    new = [(n, p) for n, p in issues if n > last_num]
    print(f"New issues to push: {[n for n, _ in new]}")

    for num, _ in new:
        print(f"Pushing issue #{num}...")
        if push_issue(num, token):
            write_state(num)
            print(f"  -> done")
        else:
            print(f"  -> failed (will retry next run)")
            break


if __name__ == "__main__":
    main()

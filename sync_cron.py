import os
import sys
import json
import re
import atexit
import time
import urllib.request
import urllib.error
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict
from difflib import SequenceMatcher

# ---------------------------------------------------------------
# CONCURRENCY LOCK
# ---------------------------------------------------------------
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync.lock")

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    pid_str = content.split(",", 1)[0]
                    pid = int(pid_str)
                    if pid != os.getpid():
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        SYNCHRONIZE = 0x00100000
                        process = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
                        if process != 0:
                            kernel32.CloseHandle(process)
                            print(f"[LOCK] Another sync process (PID {pid}) is already running. Exiting cleanly.")
                            sys.exit(0)
        except Exception:
            pass
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(f"{os.getpid()},{datetime.now().isoformat()}")
    except Exception:
        pass

def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass

acquire_lock()
atexit.register(release_lock)

# ---------------------------------------------------------------
# CONFIG & HELPERS
# ---------------------------------------------------------------
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            if '=' in _line:
                _k, _v = _line.strip().split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
MASTER_TASKS_DB = "3d2a89dd-522b-8065-83c3-e3ae61376943"
CONSISTENCY_DB = "3d3a89dd-522b-80e1-b7df-c35ba6f9e75e"

def normalize(t):
    if not t:
        return ""
    t = t.replace('\xa0', ' ').replace('…', '...').replace('–', '-').replace('—', '-')
    t = re.sub(r'cold mail (?:a|\d+)?\s*profs? everyday', 'cold mail profs everyday', t, flags=re.IGNORECASE)
    t = re.sub(r'internship outreach:\s*daily outreach sprint.*', 'daily: scout & update centralized internship application portals across all countries', t, flags=re.IGNORECASE)
    t = re.sub(r'rl:?\s*2hrs\s*(?:every\s*)?weekend\s*(\d+)', r'ai/ml/dl/rl: 2hrs every weekend \1', t, flags=re.IGNORECASE)
    t = re.sub(r'\bpack\b', 'pac', t, flags=re.IGNORECASE)
    t = re.sub(r'\bdatabas\b', 'database', t, flags=re.IGNORECASE)
    t = re.sub(r'\bmsake\b', 'make', t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip().lower()

def is_duplicate_pair(t1, t2):
    b1 = t1.get('norm_base', '')
    b2 = t2.get('norm_base', '')
    if not b1 or not b2:
        return False
    if b1 == b2:
        return True

    p1 = re.sub(r'[^\w\s]', '', b1)
    p2 = re.sub(r'[^\w\s]', '', b2)
    if p1 == p2:
        return True

    # If numbers differ (e.g. Part 1 vs Part 2 or Problem (1) vs Problem (2)), NOT duplicates
    digits1 = re.findall(r'\d+', b1)
    digits2 = re.findall(r'\d+', b2)
    if digits1 != digits2:
        return False

    ratio = SequenceMatcher(None, p1, p2).ratio()
    if ratio >= 0.88:
        return True

    return False

def extract_base_name(title_str):
    m = re.match(r"^delayed_(\d+)\s+(.*)", title_str, re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 0, title_str.strip()

def notion_request(endpoint, method="POST", data=None, max_retries=4):
    url = f"https://api.notion.com/v1/{endpoint}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    req_data = json.dumps(data).encode('utf-8') if data else None

    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 429 or e.code >= 500:
                wait = (2 ** attempt) * 0.5 + 0.3
                retry_after = e.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                print(f"  [Retry {attempt+1}/{max_retries}] HTTP {e.code} on {endpoint}, waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            print(f"HTTP Error {e.code} on {endpoint}:", body)
            return None
        except (ConnectionError, OSError, urllib.error.URLError) as e:
            wait = (2 ** attempt) * 1.0 + 0.5
            print(f"  [Retry {attempt+1}/{max_retries}] Network error on {endpoint}: {e}, waiting {wait:.1f}s...")
            time.sleep(wait)
            continue
        except Exception as e:
            print(f"Error on {endpoint}:", e)
            return None

    print(f"  [FAILED] {endpoint} after {max_retries} retries")
    return None

def get_all_pages(db_id):
    pages = []
    has_more = True
    next_cursor = None
    while has_more:
        body = {}
        if next_cursor:
            body["start_cursor"] = next_cursor
        res = notion_request(f"databases/{db_id}/query", method="POST", data=body)
        if not res:
            break
        pages.extend(res.get("results", []))
        has_more = res.get("has_more", False)
        next_cursor = res.get("next_cursor")
    return pages

today = datetime.now().strftime("%Y-%m-%d")
today_dt = datetime.now()
print(f"Running sync for {today}...")

print("Fetching all tasks...")
tasks = get_all_pages(MASTER_TASKS_DB)
print(f"Fetched {len(tasks)} tasks total.")

# ---------------------------------------------------------------
# PART 0: AUTOMATIC DEDUPLICATION & CLEANUP
# ---------------------------------------------------------------
print("\n--- PART 0: Deduplication & Cleanup ---")
by_date = defaultdict(list)
for p in tasks:
    props = p.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    d_str = date_prop.get('start') if date_prop else 'NO_DATE'
    title_prop = props.get('TASK', {}).get('title', [])
    raw_title = ''.join([t.get('plain_text', '') for t in title_prop]).strip() if title_prop else ''
    delay_count, base_name = extract_base_name(raw_title)
    norm_b = normalize(base_name)
    by_date[d_str].append({
        'id': p.get('id'),
        'raw_title': raw_title,
        'base_name': base_name,
        'norm_base': norm_b,
        'delay_count': delay_count,
        'status': props.get('STATUS', {}).get('checkbox', False),
        'has_repeat': bool(props.get('REPEAT', {}).get('select')),
        'created_time': p.get('created_time', '')
    })

archived_ids = set()
for d_str, task_list in by_date.items():
    # Clean up empty title tasks
    for t in task_list:
        if not t['raw_title'].strip():
            print(f"  Archiving empty task ({d_str}): ID {t['id']}")
            res = notion_request(f"pages/{t['id']}", method="PATCH", data={"archived": True})
            if res:
                archived_ids.add(t['id'])
            time.sleep(0.2)

    valid_tasks = [t for t in task_list if t['raw_title'].strip() and t['id'] not in archived_ids]
    clusters = []
    for t in valid_tasks:
        matched_cluster = None
        for c in clusters:
            if is_duplicate_pair(t, c[0]):
                matched_cluster = c
                break
        if matched_cluster is not None:
            matched_cluster.append(t)
        else:
            clusters.append([t])

    for cluster in clusters:
        if len(cluster) > 1:
            # Sort order: 1) status == True (keep completed), 2) has_repeat == True, 3) longer raw title, 4) earlier created
            cluster.sort(key=lambda x: (not x['status'], not x['has_repeat'], -len(x['raw_title']), x['created_time']))
            keep = cluster[0]
            for dup in cluster[1:]:
                print(f"  Archiving duplicate on {d_str}: '{dup['raw_title']}' (keeping: '{keep['raw_title']}')")
                res = notion_request(f"pages/{dup['id']}", method="PATCH", data={"archived": True})
                if res:
                    archived_ids.add(dup['id'])
                time.sleep(0.2)

if archived_ids:
    tasks = [p for p in tasks if p.get('id') not in archived_ids]
    print(f"  Cleaned up {len(archived_ids)} duplicate/empty tasks.")
else:
    print("  No duplicate tasks found on any date.")

# ---------------------------------------------------------------
# PART 1: RECURRING TASKS
# ---------------------------------------------------------------
print("\n--- PART 1: Recurring Tasks ---")

existing_today_titles = set()
existing_today_base_names = set()

for page in tasks:
    props = page.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    if not date_prop or date_prop.get('start') != today:
        continue
    title_prop = props.get('TASK', {}).get('title', [])
    if title_prop:
        raw_t = ''.join([t.get('plain_text', '') for t in title_prop]).strip()
        existing_today_titles.add(normalize(raw_t))
        _, base = extract_base_name(raw_t)
        existing_today_base_names.add(normalize(base))

# Find all recurring templates
recurring_templates = {}
for page in tasks:
    props = page.get('properties', {})
    repeat = props.get('REPEAT', {}).get('select')
    if not repeat:
        continue
    repeat_val = repeat.get('name', '')
    title_prop = props.get('TASK', {}).get('title', [])
    if not title_prop:
        continue
    base_title = ''.join([t.get('plain_text', '') for t in title_prop]).strip()
    date_prop = props.get('PLANNED DATE', {}).get('date')
    task_date = date_prop.get('start') if date_prop else None

    norm_key = normalize(base_title)
    if norm_key not in recurring_templates or (task_date and task_date > recurring_templates[norm_key].get('date', '')):
        recurring_templates[norm_key] = {
            'title': base_title,
            'repeat': repeat_val,
            'date': task_date,
            'props': props
        }

recurring_base_names = set(recurring_templates.keys())

for norm_key, tmpl in recurring_templates.items():
    title = tmpl['title']
    repeat_val = tmpl['repeat']
    last_date_str = tmpl.get('date')

    should_create = False
    title_lower = title.lower()
    last_dt = datetime.strptime(last_date_str, "%Y-%m-%d") if last_date_str else None
    days_since = (today_dt - last_dt).days if last_dt else 999

    if repeat_val == 'DAILY':
        if not last_date_str or days_since >= 1:
            should_create = True
    elif repeat_val == 'WEEKLY':
        # Determine target weekday:
        # Weekend 1 / Saturday -> Saturday (weekday 5)
        # Weekend 2 / Sunday -> Sunday (weekday 6)
        # Otherwise -> weekday of the original template
        target_weekday = None
        if "weekend 1" in title_lower or "saturday" in title_lower:
            target_weekday = 5
        elif "weekend 2" in title_lower or "sunday" in title_lower:
            target_weekday = 6
        elif last_dt:
            target_weekday = last_dt.weekday()

        if target_weekday is not None:
            # Strictly create ONLY on the matching day of the week
            if today_dt.weekday() == target_weekday and days_since >= 6:
                should_create = True
        else:
            if not last_date_str or days_since >= 7:
                should_create = True
    elif repeat_val == 'MONTHLY':
        if not last_date_str:
            should_create = True
        elif last_dt and today_dt.day == last_dt.day and days_since >= 25:
            should_create = True
        elif days_since >= 30:
            should_create = True

    if should_create and norm_key not in existing_today_titles and norm_key not in existing_today_base_names:
        props = tmpl['props']
        area = props.get("AREA", {}).get("select", {})
        priority = props.get("PRIORITY", {}).get("select", {})
        task_type = props.get("TYPE", {}).get("select", {})

        new_props = {
            "TASK": {"title": [{"text": {"content": title}}]},
            "PLANNED DATE": {"date": {"start": today}},
            "STATUS": {"checkbox": False},
            "REPEAT": {"select": {"name": repeat_val}},
            "Delayed": {"select": {"name": "No"}}
        }
        if area:
            new_props["AREA"] = {"select": {"name": area.get("name")}}
        if priority:
            new_props["PRIORITY"] = {"select": {"name": priority.get("name")}}
        if task_type:
            new_props["TYPE"] = {"select": {"name": task_type.get("name")}}

        print(f"  Creating recurring ({repeat_val}): {title}")
        res = notion_request("pages", method="POST", data={
            "parent": {"database_id": MASTER_TASKS_DB},
            "properties": new_props
        })
        if res:
            existing_today_titles.add(norm_key)
            existing_today_base_names.add(norm_key)
    elif norm_key in existing_today_titles or norm_key in existing_today_base_names:
        print(f"  Already exists today: {title}")

# ---------------------------------------------------------------
# PART 2: ROLLOVER UNCOMPLETED TASKS (delayed_X)
# ---------------------------------------------------------------
print("\n--- PART 2: Delayed Task Rollover ---")

# Refetch after recurring task creation
tasks = get_all_pages(MASTER_TASKS_DB)

# Rebuild today's active tasks and base names
existing_today_titles = set()
existing_today_base_names = set()
for page in tasks:
    props = page.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    if not date_prop or date_prop.get('start') != today:
        continue
    title_prop = props.get('TASK', {}).get('title', [])
    if title_prop:
        raw_t = ''.join([t.get('plain_text', '') for t in title_prop]).strip()
        existing_today_titles.add(normalize(raw_t))
        _, base = extract_base_name(raw_t)
        existing_today_base_names.add(normalize(base))

lineages = {}
for page in tasks:
    props = page.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    if not date_prop or not date_prop.get('start'):
        continue
    date_str = date_prop.get('start')
    status = props.get('STATUS', {}).get('checkbox', False)
    title_prop = props.get('TASK', {}).get('title', [])
    if not title_prop:
        continue
    title_str = ''.join([t.get('plain_text', '') for t in title_prop]).strip()
    repeat = props.get('REPEAT', {}).get('select')

    # Skip recurring tasks
    if repeat:
        continue

    delay_count, base_name = extract_base_name(title_str)
    norm_base = normalize(base_name)

    # CRITICAL: Recurring habit tasks are managed by Part 1 and must never be rolled over with delayed_X
    if norm_base in recurring_base_names:
        continue

    if norm_base not in lineages:
        lineages[norm_base] = []
    lineages[norm_base].append({
        "base_name": base_name,
        "norm_base": norm_base,
        "delay_count": delay_count,
        "date": date_str,
        "status": status,
        "props": props
    })

created_delayed = []
for norm_base, instances in lineages.items():
    instances.sort(key=lambda x: x["date"], reverse=True)

    # Do not create if today ALREADY has a task for this base name
    if norm_base in existing_today_base_names:
        continue

    # If the most recent instance (of any status) is dated today or later, skip
    if instances[0]["date"] >= today:
        continue

    # Check if ANY instance was completed — if the latest was completed, the user
    # is done with this task lineage. Only roll over if the latest is incomplete.
    # But also: if an OLDER instance is incomplete and a NEWER one was completed,
    # the user intentionally completed it — don't resurrect the old one.
    latest = instances[0]
    if latest["status"]:
        # Most recent instance was completed — lineage is done
        continue

    # At this point: latest instance is in the past AND incomplete → roll it over
    base_name = latest["base_name"]
    new_delay = latest["delay_count"] + 1
    new_title = f"delayed_{new_delay} {base_name}"

    if normalize(new_title) in existing_today_titles:
        continue

    props = latest["props"]
    area = props.get("AREA", {}).get("select", {})
    priority = props.get("PRIORITY", {}).get("select", {})

    new_props = {
        "TASK": {"title": [{"text": {"content": new_title}}]},
        "PLANNED DATE": {"date": {"start": today}},
        "STATUS": {"checkbox": False},
        "Delayed": {"select": {"name": "Yes"}}
    }
    if area:
        new_props["AREA"] = {"select": {"name": area.get("name")}}
    if priority:
        new_props["PRIORITY"] = {"select": {"name": priority.get("name")}}

    print(f"  Rolling over: {new_title}")
    created_delayed.append(new_title)
    time.sleep(0.35)  # Rate-limit to stay under Notion's 3 req/s cap
    res = notion_request("pages", method="POST", data={
        "parent": {"database_id": MASTER_TASKS_DB},
        "properties": new_props
    })
    if res:
        existing_today_titles.add(normalize(new_title))
        existing_today_base_names.add(norm_base)

# ---------------------------------------------------------------
# PART 3: BUILD DASHBOARD DATA
# ---------------------------------------------------------------
print("\n--- PART 3: Dashboard Data ---")

tasks = get_all_pages(MASTER_TASKS_DB)
dashboard_data = {}

for page in tasks:
    props = page.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    if not date_prop or not date_prop.get('start'):
        continue
    date_str = date_prop.get('start')
    status = props.get('STATUS', {}).get('checkbox', False)
    title_prop = props.get('TASK', {}).get('title', [])
    title_str = ''.join([t.get('plain_text', '') for t in title_prop]) if title_prop else "Untitled"

    delay_match = re.match(r"^delayed_(\d+)\s+(.*)", title_str, re.IGNORECASE)
    is_delayed = bool(delay_match)
    delay_count = int(delay_match.group(1)) if delay_match else 0

    if date_str not in dashboard_data:
        dashboard_data[date_str] = {"date": date_str, "planned": 0, "completed": 0, "tasks": []}

    dashboard_data[date_str]["planned"] += 1
    if status:
        dashboard_data[date_str]["completed"] += 1
    dashboard_data[date_str]["tasks"].append({
        "title": title_str,
        "status": status,
        "delayed": is_delayed,
        "delay_count": delay_count
    })

sorted_days = sorted(dashboard_data.values(), key=lambda x: x["date"], reverse=True)
valid_days = [d for d in sorted_days if d["planned"] > 0 and d["date"] <= today]
valid_days.reverse()
print(f"  Built data for {len(valid_days)} days.")

# ---------------------------------------------------------------
# PART 4: INJECT DATA INTO HTML & PUSH TO GITHUB
# ---------------------------------------------------------------
print("\n--- PART 4: GitHub Push ---")

repo_dir = r"C:\Users\rudra\Desktop\RUDRA\Agents\Internship-cold mail\notion-dashboard"
html_path = os.path.join(repo_dir, "index.html")

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

new_js = f"window.dashboardData = {json.dumps(valid_days, indent=2)};"
marker_start = "/* DATA_START */"
marker_end = "/* DATA_END */"

if marker_start in content and marker_end in content:
    before = content.split(marker_start)[0]
    after = content.split(marker_end)[1]
    new_content = before + marker_start + "\n" + new_js + "\n" + marker_end + after
else:
    new_content = re.sub(
        r'window\.dashboardData\s*=\s*\[[\s\S]*?\];',
        new_js,
        content
    )

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

subprocess.run("git add -A", cwd=repo_dir, shell=True)
result = subprocess.run('git commit -m "Automated data sync & rollover"', cwd=repo_dir, shell=True, capture_output=True, text=True)
if "nothing to commit" in (result.stdout + result.stderr):
    print("  No changes to push.")
else:
    push = subprocess.run("git pull origin main --rebase && git push", cwd=repo_dir, shell=True, capture_output=True, text=True)
    if push.returncode == 0:
        print("  Pushed to GitHub successfully!")
    else:
        print("  Push failed:", push.stderr)

# ---------------------------------------------------------------
# PART 5: SYNC CONSISTENCY DATABASE
# ---------------------------------------------------------------
print("\n--- PART 5: Sync Consistency Database ---")

existing_consistency = get_all_pages(CONSISTENCY_DB)
consistency_map = {}
for p in existing_consistency:
    props = p.get("properties", {})
    title_prop = props.get("Day", {}).get("title", [])
    if title_prop:
        day_str = title_prop[0].get("plain_text", "")
        consistency_map[day_str] = p.get("id")

# Sync the last 30-day window to consistency DB
sync_dates = {d["date"] for d in valid_days[-30:]}
for day_str, data in dashboard_data.items():
    if day_str not in sync_dates:
        continue
    planned = data["planned"]
    completed = data["completed"]

    props = {
        "Day": {"title": [{"text": {"content": day_str}}]},
        "DATE": {"date": {"start": day_str}},
        "PLANNED": {"number": planned},
        "COMPLETED": {"number": completed}
    }

    if day_str in consistency_map:
        page_id = consistency_map[day_str]
        notion_request(f"pages/{page_id}", method="PATCH", data={"properties": props})
    else:
        notion_request("pages", method="POST", data={
            "parent": {"database_id": CONSISTENCY_DB},
            "properties": props
        })

print("  Consistency database synced.")

print("\nSync complete!")

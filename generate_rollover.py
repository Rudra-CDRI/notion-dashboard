import sys
import json
from datetime import datetime

if len(sys.argv) < 2:
    print("[]")
    sys.exit(0)

try:
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        tasks = json.load(f)
except Exception:
    print("[]")
    sys.exit(0)

today = datetime.now().strftime("%Y-%m-%d")

# Group tasks by base name
lineages = {}

import re

for page in tasks:
    db_id = page.get('parent', {}).get('database_id', "").replace("-", "")
    if db_id != "3d2a89dd522b806583c3e3ae61376943":
        continue
        
    props = page.get('properties', {})
    date_prop = props.get('PLANNED DATE', {}).get('date')
    if not date_prop or not date_prop.get('start'):
        continue
        
    date_str = date_prop.get('start')
    status = props.get('STATUS', {}).get('checkbox', False)
    
    title_prop = props.get('TASK', {}).get('title', [])
    if not title_prop:
        continue
        
    title_str = title_prop[0].get('plain_text', '').strip()
    
    # Extract delay count and base name
    match = re.match(r"^delayed_(\d+)\s+(.*)$", title_str, re.IGNORECASE)
    if match:
        delay_count = int(match.group(1))
        base_name = match.group(2).strip()
    else:
        delay_count = 0
        base_name = title_str
        
    if base_name not in lineages:
        lineages[base_name] = []
        
    lineages[base_name].append({
        "original_title": title_str,
        "base_name": base_name,
        "delay_count": delay_count,
        "date": date_str,
        "status": status,
        "props": props # keep props to copy area/priority etc if needed
    })

actions = []

for base_name, instances in lineages.items():
    # Sort instances by date to find the absolute latest one
    instances.sort(key=lambda x: x["date"], reverse=True)
    latest = instances[0]
    
    # If the latest instance is in the past, and it's incomplete
    if latest["date"] < today and not latest["status"]:
        new_delay = latest["delay_count"] + 1
        new_title = f"delayed_{new_delay} {base_name}"
        
        # Make sure we didn't already plan to create it (shouldn't happen but safe)
        actions.append({
            "action": "create",
            "title": new_title,
            "date": today,
            "area": latest["props"].get("AREA", {}).get("select", {}).get("name") if latest["props"].get("AREA", {}).get("select") else None,
            "priority": latest["props"].get("PRIORITY", {}).get("select", {}).get("name") if latest["props"].get("PRIORITY", {}).get("select") else None
        })

print(json.dumps(actions, indent=2))

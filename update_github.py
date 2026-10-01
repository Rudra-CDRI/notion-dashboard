import sys
import json
import subprocess

if len(sys.argv) < 2:
    print("No json data provided")
    sys.exit(1)

json_data_str = sys.argv[1]

# Make sure it's valid JSON
try:
    data = json.loads(json_data_str)
except Exception as e:
    print("Invalid JSON")
    sys.exit(1)

# Path to the repo
repo_dir = r"C:\Users\rudra\Desktop\RUDRA\Agents\Internship-cold mail\notion-dashboard"
html_path = repo_dir + r"\dashboard_standalone.html"

# Read HTML and find the injected data to replace
with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

import re
# Replace the window.dashboardData block
new_js = f"window.dashboardData = {json.dumps(data, indent=2)};"
new_content = re.sub(r'window\.dashboardData\s*=\s*\[.*?\];', new_js, content, flags=re.DOTALL)

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

# Git push
subprocess.run("git add dashboard_standalone.html", cwd=repo_dir, shell=True)
subprocess.run('git commit -m "Automated data sync"', cwd=repo_dir, shell=True)
res = subprocess.run("git pull origin main --rebase && git push", cwd=repo_dir, shell=True, capture_output=True, text=True)

if res.returncode == 0:
    print("Successfully pushed to GitHub!")
else:
    print("Git error:", res.stderr)

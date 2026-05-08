"""ProjectRegistry — persists built projects to ~/MashProjects/.registry.json"""
import json
import os
from datetime import datetime

REGISTRY_PATH = os.path.expanduser("~/MashProjects/.registry.json")


def save(name: str, path: str):
    """Add or update a project entry in the registry."""
    projects = load_all()
    # Remove old entry for same path
    projects = [p for p in projects if p.get("path") != path]
    projects.insert(0, {
        "name": name,
        "path": path,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(projects, f, indent=2)


def load_all() -> list:
    """Return all saved projects, newest first."""
    if not os.path.exists(REGISTRY_PATH):
        return []
    try:
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def find(query: str) -> dict | None:
    """Fuzzy-find a project by name fragment. Returns the best match or None."""
    query = query.lower().strip()
    projects = load_all()
    # Exact name match first
    for p in projects:
        if p["name"].lower() == query:
            return p
    # Partial match
    for p in projects:
        if query in p["name"].lower() or p["name"].lower() in query:
            return p
    # Word-by-word match
    words = query.split()
    for p in projects:
        if any(w in p["name"].lower() for w in words if len(w) > 2):
            return p
    return None


def scan_existing():
    """Scan ~/MashProjects/ for folders not yet in registry and register them."""
    root = os.path.expanduser("~/MashProjects")
    if not os.path.isdir(root):
        return
    registered_paths = {p["path"] for p in load_all()}
    for entry in sorted(os.scandir(root), key=lambda e: e.stat().st_mtime, reverse=True):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.path in registered_paths:
            continue
        # Derive clean name by stripping timestamp suffix (_YYYYMMDD_HHMMSS)
        parts = entry.name.rsplit("_", 2)
        name = parts[0] if len(parts) == 3 and parts[1].isdigit() else entry.name
        mtime = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        # Append directly to avoid re-loading/re-saving in a loop
        projects = load_all()
        projects.append({"name": name, "path": entry.path, "created": mtime})
        os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
        with open(REGISTRY_PATH, "w") as f:
            json.dump(projects, f, indent=2)
        registered_paths.add(entry.path)

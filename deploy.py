"""
Cloud deployment — GitHub + Vercel auto-deploy.
================================================
After QA passes, push the generated project to a new GitHub repo
and deploy it to Vercel. Returns the live production URL.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import base64
from pathlib import Path

from config import settings


def _run(cmd: str, cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    r = subprocess.run(
        cmd, cwd=cwd, shell=True, capture_output=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _slugify(name: str) -> str:
    """Convert project name to a URL-safe repo/project slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "project"


def _git_init_and_push(project_dir: str, repo_name: str) -> str | None:
    """Initialize git, create GitHub repo, push code. Returns repo URL or None."""
    token = settings.github_token
    if not token:
        return None

    # Initialize git repo if needed
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.isdir(git_dir):
        _run("git init", cwd=project_dir)

    # Create .gitignore if missing
    gitignore = os.path.join(project_dir, ".gitignore")
    if not os.path.isfile(gitignore):
        Path(gitignore).write_text(
            "node_modules/\n.next/\n.env\n.env.local\n*.log\n", encoding="utf-8"
        )

    # Stage and commit
    _run("git add -A", cwd=project_dir)
    _run('git commit -m "Initial commit \u2014 auto-deployed by Aigentic Orbit"', cwd=project_dir)

    # Create GitHub repo via CLI (gh) or API
    repo_url = _create_github_repo(repo_name, token)
    if not repo_url:
        return None

    # Set remote and push without storing token in git remote config.
    _run("git remote remove origin", cwd=project_dir)  # in case already set
    repo_path = repo_url.split("github.com/")[-1]
    remote_url = f"https://github.com/{repo_path}"
    _run(f'git remote add origin "{remote_url}"', cwd=project_dir)

    auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    rc, out, err = _run(
        f'git -c http.https://github.com/.extraheader="AUTHORIZATION: basic {auth}" push -u origin HEAD:main',
        cwd=project_dir,
        timeout=180,
    )
    if rc != 0:
        print(f"  ⚠ git push failed: {err[:300]}")
        return None

    return repo_url


def _create_github_repo(name: str, token: str) -> str | None:
    """Create a new GitHub repo via the REST API. Returns 'https://github.com/user/repo' or None."""
    import urllib.request

    # First, get the authenticated user
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            user_data = json.loads(resp.read())
            username = user_data["login"]
    except Exception as e:
        print(f"  ⚠ GitHub auth failed: {e}")
        return None

    # Check if repo already exists
    check_req = urllib.request.Request(
        f"https://api.github.com/repos/{username}/{name}",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
    )
    try:
        with urllib.request.urlopen(check_req, timeout=15):
            # Repo exists, return its URL
            return f"https://github.com/{username}/{name}"
    except Exception:
        pass  # 404 = doesn't exist, proceed to create

    # Create the repo
    payload = json.dumps({"name": name, "private": False, "auto_init": False}).encode()
    create_req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(create_req, timeout=15) as resp:
            repo_data = json.loads(resp.read())
            return repo_data.get("html_url", f"https://github.com/{username}/{name}")
    except Exception as e:
        print(f"  ⚠ GitHub repo creation failed: {e}")
        return None


def _deploy_to_vercel(project_dir: str, project_name: str) -> str | None:
    """Deploy to Vercel via CLI and return the production URL."""
    token = settings.vercel_token
    if not token:
        return None

    env = os.environ.copy()
    env["VERCEL_TOKEN"] = token

    # Link/deploy with Vercel CLI (non-interactive)
    rc, out, err = _run(
        f'npx vercel --prod --yes --token "{token}"',
        cwd=project_dir, timeout=300,
    )

    if rc != 0 and "https://" not in out and "https://" not in err:
        # Retry with --yes to auto-accept npx install
        rc, out, err = _run(
            f'npx --yes vercel --prod --yes --token "{token}"',
            cwd=project_dir, timeout=300,
        )

    # Combine stdout and stderr for URL extraction (Vercel sometimes puts URL in stderr)
    combined = out + "\n" + err

    if rc != 0 and "https://" not in combined:
        print(f"  ⚠ Vercel deploy failed: {err[:300]}")
        return None

    # Extract the production URL from output
    # Vercel outputs lines like "Production: https://my-app.vercel.app [5s]"
    lines = combined.strip().splitlines()
    for line in lines:
        line = line.strip()
        if "production:" in line.lower():
            # Extract URL from "Production: https://xxx [2s]"
            parts = line.split()
            for p in parts:
                if p.startswith("https://"):
                    return p.rstrip("[]0123456789s")
    # Fallback: find any https:// URL that looks like a Vercel deploy
    for line in reversed(lines):
        line = line.strip()
        for word in line.split():
            if word.startswith("https://") and ("vercel" in word or ".app" in word):
                return word.rstrip("[]0123456789s")

    return None


def deploy_project(project_dir: str, project_name: str) -> dict:
    """
    Full deploy pipeline: push to GitHub → deploy to Vercel.
    Returns dict with keys: github_url, vercel_url, deployed, error
    """
    result = {
        "deployed": False,
        "github_url": None,
        "vercel_url": None,
        "error": None,
    }

    slug = _slugify(project_name)

    # Validate tokens
    if not settings.github_token:
        result["error"] = "No github_token configured in .env"
        return result
    if not settings.vercel_token:
        result["error"] = "No vercel_token configured in .env"
        return result

    print(f"\n🚀 Deploying '{project_name}' to cloud...")

    # Step 1: Push to GitHub
    print(f"  📦 Pushing to GitHub repo: {slug}")
    github_url = _git_init_and_push(project_dir, slug)
    if github_url:
        result["github_url"] = github_url
        print(f"  ✓ GitHub: {github_url}")
    else:
        result["error"] = "Failed to push to GitHub"
        print(f"  ✗ GitHub push failed")
        return result

    # Step 2: Deploy to Vercel
    print(f"  ☁ Deploying to Vercel...")
    vercel_url = _deploy_to_vercel(project_dir, slug)
    if vercel_url:
        result["vercel_url"] = vercel_url
        result["deployed"] = True
        print(f"  ✓ Live at: {vercel_url}")
    else:
        result["error"] = "GitHub pushed but Vercel deploy failed"
        print(f"  ✗ Vercel deploy failed (project is on GitHub)")

    return result

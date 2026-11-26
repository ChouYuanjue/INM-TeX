"""Publish one canonical INM edition to the TeX repo, personal site and 3D reader.

Run from any directory with the INM Python environment:
    python scripts/sync_release.py --publish
Without --publish, build and validate the three local trees, but do not commit/push.
Use --reuse-build only after a successful full LaTeX build. Use --resume to
resume a previously prepared release (including a failed network publication).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import base64

import fitz
import requests

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build-log"
STATE = WORK / "sync-release-state.json"
REPO_3D = "ChouYuanjue/inm-3d-book"
SOURCE_PATHS = ["tex", "figures", "inm.tex", "volumes-index.tex", "notes-index.tex",
                "images.bib", "cover-art.png", ".latexmkrc", "inm.pdf",
                "inm-release.json", "scripts/sync_release.py", "README.md"]
SITE_PATHS = ["public/notes", "data/inm_notes_index.json"]
BOOK_PATHS = ["public/inm.pdf", "public/outline.json", "public/inm-release.json",
              "public/page-previews", "src/page-preview-index.json", "src/main.js",
              "src/toc.js", "src/binding.js", "index.html", "README.md", "docs"]
MANIFEST = "inm-release.json"

def say(*args):
    print(*args, flush=True)

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not p.exists() or p.read_text(encoding="utf-8") != payload:
        p.write_text(payload, encoding="utf-8", newline="\n")

def sha256(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def run(args, cwd=ROOT, capture=False, env=None):
    args = [str(x) for x in args]
    if args[0] == "npm" and os.name == "nt":
        args[0] = shutil.which("npm.cmd") or "npm.cmd"
    p = subprocess.run(args, cwd=cwd, env=env, check=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE if capture else None)
    return p.stdout.strip() if capture else None

def git(cwd, *args, capture=True):
    return run(["git", *args], cwd=cwd, capture=capture)

def tracked(cwd):
    return set(git(cwd, "ls-files", "-z").split("\0")) - {""}

def within(path, paths):
    return any(path == q or path.startswith(q.rstrip("/") + "/") for q in paths)

def check_repo(cwd, repository, branch=None):
    require(cwd.is_dir(), f"Missing checkout: {cwd}")
    remote = git(cwd, "remote", "get-url", "origin")
    require(remote.rstrip("/").removesuffix(".git").lower().endswith(repository.lower()),
            f"Unexpected origin for {cwd}: {remote}")
    if branch:
        require(git(cwd, "branch", "--show-current") == branch, f"Expected {branch} in {cwd}")
    require(not git(cwd, "diff", "--cached", "--name-only"), f"Unrelated staged changes in {cwd}")

def fast_forward_check(cwd, branch):
    head = git(cwd, "rev-parse", "HEAD")
    remote = git(cwd, "ls-remote", "--heads", "origin", f"refs/heads/{branch}").split()
    require(remote, f"Cannot resolve origin/{branch}")
    parent = remote[0]
    if head != parent:
        p = subprocess.run(["git", "merge-base", "--is-ancestor", parent, head], cwd=cwd)
        require(p.returncode == 0, f"{cwd}: remote branch differs; fetch/reconcile before publishing.")
    return parent

def tool_env():
    env = os.environ.copy()
    if os.name == "nt":
        home = Path.home()
        paths = [Path(env.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "Programs/MiKTeX/miktex/bin/x64",
                 home / "scoop/apps/perl/current/perl/bin",
                 home / "scoop/apps/perl/current/perl/site/bin",
                 Path(sys.executable).parent, Path(sys.executable).parent / "Scripts",
                 Path(sys.executable).parent / "Library/bin"]
        env["PATH"] = os.pathsep.join([str(p) for p in paths if p.exists()] + [env.get("PATH", "")])
    return env

def compile_and_validate(reuse, workers):
    env = tool_env()
    if not reuse:
        run([sys.executable, "scripts/compile_missing_note_pdfs.py", "--clean-workdirs",
             "--workers", str(workers)], env=env)
        # Do not substitute a stale PDF if the destination is locked.
        latexmk = shutil.which("latexmk", path=env["PATH"]) or "latexmk"
        run([latexmk, "-interaction=nonstopmode", "-halt-on-error"], env=env)
    notes = sorted((ROOT / "tex/notes").glob("N-????????.tex"))
    require(notes, "No notes found")
    full = ROOT / "inm.pdf"
    require(full.is_file(), "Missing canonical inm.pdf")
    sources = [ROOT / "inm.tex", ROOT / "volumes-index.tex", *list((ROOT / "tex").rglob("*.tex"))]
    # Ignore LaTeX-generated auxiliaries; include figure sources and binary artwork.
    sources.extend(p for p in (ROOT / "figures").rglob("*") if p.is_file() and
                   p.suffix.lower() in {".tex", ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".asy"})
    require(full.stat().st_mtime + 2 >= max(p.stat().st_mtime for p in sources),
            "Full PDF is older than a manuscript source. Compile before publication.")
    note_items = []
    with fitz.open(full) as d:
        require(not d.is_repaired and len(d) > 0, "Full manuscript is damaged")
        pages = len(d)
        toc = d.get_toc()
        anchors = {title for _, title, _ in toc if re.fullmatch(r"N-\d{8}", title)}
        ids = {n.stem for n in notes}
        require(anchors == ids, f"Book bookmark/note mismatch: {sorted(ids - anchors)}, {sorted(anchors - ids)}")
    for n in notes:
        p = ROOT / "single-pdfs" / (n.stem + ".pdf")
        require(p.is_file() and p.stat().st_mtime + 2 >= n.stat().st_mtime,
                f"Missing or stale standalone PDF: {n.stem}")
        with fitz.open(p) as doc:
            require(not doc.is_repaired and len(doc) > 0, f"Damaged note PDF: {n.stem}")
            require(n.stem in doc[0].get_text(), f"Wrong standalone note ID: {n.stem}")
            note_items.append({"id": n.stem, "pages": len(doc), "pdf_sha256": sha256(p),
                               "tex_sha256": sha256(n)})
    release = {"schema": 1, "pdf_sha256": sha256(full), "pages": pages,
               "bookmarks": len(toc), "note_count": len(notes), "notes": note_items}
    write_json(ROOT / MANIFEST, release)
    say(f"Canonical edition: {pages} pages / {len(notes)} notes / SHA256 {release['pdf_sha256']}")
    return release

def copy_if_different(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or src.stat().st_size != dst.stat().st_size or sha256(src) != sha256(dst):
        shutil.copy2(src, dst)

def sync_site(site, release, build=False):
    env = os.environ.copy()
    env["INM_TEX_DIR"] = str(ROOT)
    run(["node", "scripts/generate-notes-index.js"], cwd=site, env=env)
    copy_if_different(ROOT / MANIFEST, site / "public/notes" / MANIFEST)
    validate_site(site, release)
    if build:
        run(["npm", "run", "build"], cwd=site)
    say("Personal-site archive verified")


def validate_site(site, release):
    require(json.loads((site / "public/notes" / MANIFEST).read_text(encoding="utf-8")) == release,
            "Website release manifest differs")
    index = json.loads((site / "data/inm_notes_index.json").read_text(encoding="utf-8"))
    require(index["project"]["noteCount"] == release["note_count"], "Website note count mismatch")
    require({x["id"] for x in index["notes"]} == {x["id"] for x in release["notes"]},
            "Website note IDs differ")
    require(sha256(site / "public/notes/inm.pdf") == release["pdf_sha256"], "Website full PDF differs")
    def expected_url(path, digest):
        return f"{path}?edition={digest[:12]}"
    require(index["project"]["fullPdf"] == expected_url("/notes/inm.pdf", release["pdf_sha256"]),
            "Website full PDF link lacks the canonical revision")
    indexed = {row["id"]: row for row in index["notes"]}
    for row in release["notes"]:
        id = row["id"]
        dst = site / "public/notes" / id[2:6] / id
        require(sha256(dst.with_suffix(".pdf")) == row["pdf_sha256"], f"Website PDF differs: {id}")
        require(sha256(dst.with_suffix(".tex")) == row["tex_sha256"], f"Website TeX differs: {id}")
        url = f"/notes/{id[2:6]}/{id}"
        require(indexed[id]["pdf"] == expected_url(url + ".pdf", row["pdf_sha256"]),
                f"Website PDF link has a stale revision: {id}")
        require(indexed[id]["tex"] == expected_url(url + ".tex", row["tex_sha256"]),
                f"Website TeX link has a stale revision: {id}")


def edit_text(path, fn):
    # Preserve the checkout's newline convention; avoid unrelated source changes.
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    new = fn(text)
    if new != text:
        path.write_bytes(new.encode("utf-8"))

def patch_book_defaults(book, old_pages, old_bookmarks, release):
    pages = release["pages"]
    old_n, new_n = f"{old_pages:,}", f"{pages:,}"
    old_thick, new_thick = f"{((old_pages + 1)//2)*.1:.1f}", f"{((pages + 1)//2)*.1:.1f}"
    def html(s):
        s = s.replace(old_n, new_n)
        s = s.replace(f'value="{old_pages}"', f'value="{pages}"')
        s = s.replace(f'max="{old_pages}"', f'max="{pages}"')
        s = s.replace(f'id="coreThickness">{old_thick}<', f'id="coreThickness">{new_thick}<')
        s = s.replace(f'{old_thick} mm book block', f'{new_thick} mm book block')
        return s
    edit_text(book / "index.html", html)
    edit_text(book / "src/main.js", lambda s: re.sub(
        r"const SOURCE_PAGES=\d+;", "const SOURCE_PAGES=pagePreviewIndex.count;", s))
    edit_text(book / "src/binding.js", lambda s: re.sub(r"sourcePages=\d+,printPages=sourcePages",
                      f"sourcePages={pages},printPages=sourcePages", s))
    # Bust outline cache using the same revision as the PDF and preview packs.
    edit_text(book / "src/toc.js", lambda s: s.replace(
        "initBookOutline({baseUrl, onJump})", "initBookOutline({baseUrl, onJump, revision})").replace(
        "fetch(baseUrl+'outline.json')", "fetch(baseUrl+'outline.json'+(revision?'?edition='+encodeURIComponent(revision):''))"))
    edit_text(book / "src/main.js", lambda s: re.sub(
        r"initBookOutline\(\{baseUrl:import\.meta\.env\.BASE_URL,onJump:navigateToPage(?:,revision:pagePreviewIndex\.source)?\}\)",
        "initBookOutline({baseUrl:import.meta.env.BASE_URL,onJump:navigateToPage,revision:pagePreviewIndex.source})", s))
    edit_text(book / "README.md", lambda s: s.replace(old_n, new_n)
              .replace(f"{old_bookmarks:,} PDF outline entries", f"{release['bookmarks']:,} PDF outline entries")
              .replace(f"{old_thick} mm book block", f"{new_thick} mm book block")
              .replace("one complete, versioned 16.4 MB PDF", "one complete, versioned PDF"))

def validate_preview(book, release):
    p = book / "public/page-previews/index.json"
    index = json.loads(p.read_text(encoding="utf-8"))
    require(index == json.loads((book / "src/page-preview-index.json").read_text(encoding="utf-8")),
            "Bundled and served preview indices differ")
    pdf = (book / "public/inm.pdf").read_bytes()
    for tier, quality in [(index, 82), (index["mobile"], 76)]:
        expected = hashlib.sha256(pdf + f"webp-{tier['height']}-{quality}-v2".encode()).hexdigest()[:12]
        require(tier["source"] == expected, "Preview cache revision is stale")
        require(tier["count"] == release["pages"] and len(tier["pages"]) == release["pages"]+1,
                "Preview page count mismatch")
        files = [book / "public/page-previews" / name for name in tier["packs"]]
        for f in files:
            with f.open("rb") as stream:
                require(stream.read(8) == b"INMPG001", f"Invalid preview pack: {f}")
        sizes = [f.stat().st_size for f in files]
        for chunk, offset, length, width, height in tier["pages"][1:]:
            require(0 <= chunk < len(files) and offset >= 8 and length > 0 and
                    offset + length <= sizes[chunk] and width > 0 and height > 0,
                    "Preview index points outside a pack")
    return index

def sync_book(book, release):
    old_outline = json.loads((book / "public/outline.json").read_text(encoding="utf-8"))
    old_pages, old_bookmarks = old_outline["pages"], len(old_outline["items"])
    copy_if_different(ROOT / "inm.pdf", book / "public/inm.pdf")
    copy_if_different(ROOT / MANIFEST, book / "public" / MANIFEST)
    run([sys.executable, "scripts/extract_outline.py"], cwd=book)
    outline = json.loads((book / "public/outline.json").read_text(encoding="utf-8"))
    require(outline["pages"] == release["pages"] and len(outline["items"]) == release["bookmarks"],
            "3D outline differs from canonical PDF")
    patch_book_defaults(book, old_pages, old_bookmarks, release)
    try:
        preview = validate_preview(book, release)
        say("Preview packs already match the canonical PDF")
    except (RuntimeError, OSError, KeyError, ValueError, TypeError):
        run([sys.executable, "scripts/build_page_previews.py"], cwd=book)
        preview = validate_preview(book, release)
    say("3D cache revision:", preview["source"])
    run(["npm", "test"], cwd=book)
    run(["npm", "run", "build"], cwd=book)
    # docs is the actual Pages deployment; synchronize only the built site.
    docs, dist = book / "docs", book / "dist"
    require((dist / "index.html").is_file() and (dist / "inm.pdf").is_file(),
            "Missing Vite output")
    dest_files = {p.relative_to(dist) for p in dist.rglob("*") if p.is_file()}
    for p in docs.rglob("*"):
        if p.is_file() and p.relative_to(docs) not in dest_files and p.name != ".nojekyll":
            p.unlink()
    for rel in sorted(dest_files):
        copy_if_different(dist / rel, docs / rel)
    (docs / ".nojekyll").touch()
    require(sha256(book / "public/inm.pdf") == release["pdf_sha256"] ==
            sha256(docs / "inm.pdf"), "3D source/deployed PDF differs")
    require((docs / "outline.json").read_bytes() == (book / "public/outline.json").read_bytes(),
            "3D served outline differs")
    require((docs / "page-previews/index.json").read_bytes() ==
            (book / "public/page-previews/index.json").read_bytes(), "3D served previews differ")
    for name in preview["packs"] + preview["mobile"]["packs"]:
        require(sha256(docs / "page-previews" / name) ==
                sha256(book / "public/page-previews" / name), f"3D pack differs: {name}")
    say("3D reader verified (PDF, outline, both preview tiers, tests and static build)")

class BookApi:
    def __init__(self):
        self.token = run(["gh", "auth", "token"], capture=True)
        require(self.token, "GitHub CLI must be authenticated")
        self.base = f"https://api.github.com/repos/{REPO_3D}"
    def request(self, method, endpoint, data=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        for attempt in range(4):
            try:
                r = requests.request(method, self.base + endpoint, headers=headers, json=data,
                                     timeout=(20, 180))
                if r.ok:
                    return r.json()
                if r.status_code < 500 and r.status_code not in {429}:
                    raise RuntimeError(f"GitHub HTTP {r.status_code} at {endpoint}: {r.text[:160]}")
            except requests.RequestException as exc:
                if attempt == 3:
                    raise RuntimeError(f"GitHub network failure at {endpoint}") from exc
            time.sleep(2**attempt)
        raise RuntimeError(f"GitHub request failed at {endpoint}")
    def snapshot(self):
        parent = self.request("GET", "/git/ref/heads/main")["object"]["sha"]
        tree = self.request("GET", "/git/commits/" + parent)["tree"]["sha"]
        data = self.request("GET", "/git/trees/" + tree + "?recursive=1")
        require(not data.get("truncated"), "Remote tree listing incomplete")
        return parent, tree, {x["path"]: x["sha"] for x in data["tree"] if x["type"] == "blob"}
    def preflight(self, book, resume, prior):
        parent, tree, remote = self.snapshot()
        # The working tree is the latest production reference, not the older prototype checkout.
        local_paths = tracked(book)
        diff = [p for p in sorted(set(remote) | local_paths)
                if p not in remote or not (book / p).is_file() or
                git_blob((book / p).read_bytes()) != remote[p]]
        if diff:
            permitted = (resume and prior.get("book_parent") == parent and
                         prior.get("book_dir") == str(book) and all(within(p, BOOK_PATHS) for p in diff))
            require(permitted, "3D checkout is not the published production tree. "
                    "Reconcile it first; use --resume only for an interrupted release. "
                    f"Differing paths: {diff[:10]}")
        return parent, tree, remote
    def publish(self, book, baseline, message):
        parent, tree, remote = baseline
        require(self.request("GET", "/git/ref/heads/main")["object"]["sha"] == parent,
                "3D main changed during preparation. No forced update.")
        local_paths = tracked(book)
        changes, blobs = [], {}
        known = set(remote.values())
        for p in sorted(set(remote) | local_paths):
            if not within(p, BOOK_PATHS):
                continue
            file = book / p
            if p not in local_paths or not file.is_file():
                if p in remote:
                    changes.append({"path": p, "mode": "100644", "type": "blob", "sha": None})
                continue
            content = file.read_bytes()
            sha = git_blob(content)
            if remote.get(p) == sha:
                continue
            changes.append({"path": p, "mode": "100644", "type": "blob", "sha": sha})
            if sha not in known:
                blobs.setdefault(sha, file)
        if not changes:
            say("3D main already contains this edition", parent)
            return parent
        say(f"3D API publication: {len(changes)} changed paths, {len(blobs)} unique new blobs")
        def upload(item):
            sha, path = item
            response = self.request("POST", "/git/blobs",
                                    {"content": base64.b64encode(path.read_bytes()).decode("ascii"),
                                     "encoding": "base64"})
            require(response["sha"] == sha, f"Remote blob hash mismatch: {path.name}")
            return path.name
        with ThreadPoolExecutor(max_workers=6) as pool:
            tasks = [pool.submit(upload, item) for item in blobs.items()]
            for i, job in enumerate(as_completed(tasks), 1):
                name = job.result()
                if i == 1 or i % 8 == 0 or i == len(tasks):
                    say(f"Uploaded {i}/{len(tasks)} blobs ({name})")
        new_tree = self.request("POST", "/git/trees", {"base_tree": tree, "tree": changes})["sha"]
        commit = self.request("POST", "/git/commits", {"message": message, "tree": new_tree,
                                                       "parents": [parent]})["sha"]
        updated = self.request("PATCH", "/git/refs/heads/main", {"sha": commit, "force": False})
        require(updated["object"]["sha"] == commit, "3D publication ref mismatch")
        _, _, published = self.snapshot()
        for path in ["docs/inm.pdf", "public/inm.pdf", "docs/inm-release.json",
                     "docs/outline.json", "docs/page-previews/index.json"]:
            require(published[path] == git_blob((book / path).read_bytes()), f"3D remote mismatch: {path}")
        say("3D main published:", commit)
        return commit

def commit_scoped(cwd, paths, message):
    require(not git(cwd, "diff", "--cached", "--name-only"), f"Staged changes in {cwd}")
    exists = [p for p in paths if (cwd / p).exists() or
              any(within(x, [p]) for x in tracked(cwd))]
    git(cwd, "add", "-A", "--", *exists, capture=False)
    staged = git(cwd, "diff", "--cached", "--name-only").splitlines()
    require(all(within(x, paths) for x in staged), f"Unexpected staged file in {cwd}")
    if staged:
        git(cwd, "commit", "-m", message, capture=False)
    return git(cwd, "rev-parse", "HEAD")

def push_verify(cwd, branch, commit):
    git(cwd, "push", "origin", f"HEAD:refs/heads/{branch}", capture=False)
    actual = git(cwd, "ls-remote", "--heads", "origin", f"refs/heads/{branch}").split()[0]
    require(actual == commit, f"Remote verification failed for {cwd}")
    say(f"Published {cwd.name}/{branch}: {commit}")
    return actual

def already_published(site, book, release, prior, baseline, source_parent, site_parent):
    """Skip generation and publication only for a verified, unchanged release."""
    if (prior.get("status") != "published" or
            prior.get("pdf_sha256") != release["pdf_sha256"] or
            prior.get("book_remote") != baseline[0]):
        return False
    for cwd, paths, parent in [(ROOT, SOURCE_PATHS, source_parent),
                               (site, SITE_PATHS, site_parent),
                               (book, BOOK_PATHS, None)]:
        if parent and git(cwd, "rev-parse", "HEAD") != parent:
            return False
        if git(cwd, "status", "--porcelain", "--untracked-files=all", "--", *paths):
            return False
    try:
        validate_site(site, release)
        for dest in (book / "public", book / "docs"):
            require(json.loads((dest / MANIFEST).read_text(encoding="utf-8")) == release,
                    "3D release manifest differs")
            require(sha256(dest / "inm.pdf") == release["pdf_sha256"], "3D PDF differs")
            outline = json.loads((dest / "outline.json").read_text(encoding="utf-8"))
            require(outline["pages"] == release["pages"] and len(outline["items"]) == release["bookmarks"],
                    "3D outline differs")
        preview = validate_preview(book, release)
        require((book / "docs/page-previews/index.json").read_bytes() ==
                (book / "public/page-previews/index.json").read_bytes(), "Deployed preview index differs")
        for name in preview["packs"] + preview["mobile"]["packs"]:
            require(sha256(book / "docs/page-previews" / name) ==
                    sha256(book / "public/page-previews" / name), f"Deployed pack differs: {name}")
    except (RuntimeError, OSError, KeyError, ValueError, TypeError) as exc:
        say("Existing release needs synchronization:", exc)
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--homepage", type=Path, default=ROOT.parent / ".homepage")
    parser.add_argument("--book-dir", type=Path, default=ROOT.parent / "INM-3D-Book-publish-reference")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--reuse-build", action="store_true")
    parser.add_argument("--homepage-build", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    site, book = args.homepage.resolve(), args.book_dir.resolve()
    WORK.mkdir(exist_ok=True)
    check_repo(ROOT, "ChouYuanjue/INM-TeX", "main")
    check_repo(site, "ChouYuanjue/bibliotheca-runnel", "master")
    check_repo(book, REPO_3D)
    source_parent = site_parent = None
    if args.publish:
        source_parent = fast_forward_check(ROOT, "main")
        site_parent = fast_forward_check(site, "master")
    prior = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    api = BookApi()
    baseline = api.preflight(book, args.resume, prior)
    release = compile_and_validate(args.reuse_build, args.workers)
    if args.publish and not args.homepage_build and already_published(
            site, book, release, prior, baseline, source_parent, site_parent):
        write_json(STATE, {**prior, "source_commit": source_parent, "source_remote": source_parent,
                           "homepage_commit": site_parent, "homepage_remote": site_parent})
        say("Already published and verified; skipped archive generation, preview builds, commits and pushes.")
        return
    state = {"book_parent": baseline[0], "book_dir": str(book),
             "pdf_sha256": release["pdf_sha256"], "status": "preparing"}
    if args.resume and prior.get("book_parent") == baseline[0]:
        require(prior.get("pdf_sha256") == release["pdf_sha256"],
                "The canonical PDF changed since preparation. Reconcile the interrupted edition first.")
    write_json(STATE, state)
    sync_site(site, release, args.homepage_build)
    sync_book(book, release)
    state["status"] = "verified"
    write_json(STATE, state)
    if not args.publish:
        say("All local assets agree. Run with --reuse-build --resume --publish to publish.")
        return
    message = f"Sync INM edition {release['pdf_sha256'][:12]} ({release['pages']} pages, {release['note_count']} notes)"
    source_commit = commit_scoped(ROOT, SOURCE_PATHS, message)
    site_commit = commit_scoped(site, SITE_PATHS, message)
    book_commit = commit_scoped(book, BOOK_PATHS, message)
    state.update({"source_commit": source_commit, "homepage_commit": site_commit, "book_local_commit": book_commit})
    write_json(STATE, state)
    state["source_remote"] = push_verify(ROOT, "main", source_commit)
    write_json(STATE, state)
    state["homepage_remote"] = push_verify(site, "master", site_commit)
    write_json(STATE, state)
    state["book_remote"] = api.publish(book, baseline, message)
    state["status"] = "published"
    write_json(STATE, state)
    say("Three repositories published; edition SHA256:", release["pdf_sha256"])

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        say(f"SYNC FAILED: {exc}")
        raise SystemExit(1) from exc

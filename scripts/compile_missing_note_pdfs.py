#!/usr/bin/env python3
"""Compile missing or outdated INM notes as standalone PDFs.

This script does not use the collective book build.  For each
``tex/notes/N-YYYYMMDD.tex`` file, it creates a temporary wrapper that inputs
only that note, compiles the wrapper with ``latexmk -xelatex``, and copies the
result to ``single-pdfs/N-YYYYMMDD.pdf``.

Default rebuild rule:
    build if the PDF is missing, or if the source .tex file is newer than the PDF.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NoteJob:
    note_id: str
    source: Path
    pdf: Path


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    root_default = here.parent

    parser = argparse.ArgumentParser(
        description="Compile INM notes as standalone PDFs when missing or outdated."
    )
    parser.add_argument("--root", default=str(root_default), help="project root")
    parser.add_argument("--notes-dir", default="tex/notes", help="notes directory")
    parser.add_argument("--output-dir", default="single-pdfs", help="standalone PDF output directory")
    parser.add_argument("--build-dir", default="build-log/single-note-build", help="temporary build directory")
    parser.add_argument("--force", action="store_true", help="rebuild every note even if the PDF is current")
    parser.add_argument("--list-only", action="store_true", help="list queued notes without compiling")
    parser.add_argument("--clean-workdirs", action="store_true", help="delete each per-note work directory after success")
    parser.add_argument("--verbose-latexmk", action="store_true", help="show latexmk output instead of capturing it")
    parser.add_argument("--limit", type=int, default=0, help="compile/list only the first N queued notes")
    parser.add_argument("--workers", type=int, default=1, help="independent note build workers (default: 1)")
    return parser.parse_args()


def info(message: str) -> None:
    print(f"[single-note] {message}", flush=True)


def needs_build(source: Path, pdf: Path, force: bool) -> bool:
    if force:
        return True
    if not pdf.exists():
        return True
    return source.stat().st_mtime > pdf.stat().st_mtime


def make_wrapper(note_id: str) -> str:
    note_rel = f"tex/notes/{note_id}"
    return rf"""\documentclass[11pt,twoside=semi,openany,numbers=noenddot,titlepage=false]{{scrbook}}
\newcommand{{\inmversion}}{{INM single-note draft \the\year-\twodigits\month-\twodigits\day}}
\input{{tex/preamble}}
\input{{tex/macros}}
\input{{tex/Qcircuit.tex}}
\input{{tex/note-style.tex}}
\title{{Informal Notes on Mathematics}}
\subtitle{{{note_id}}}
\author{{Runnel Zhang}}
\date{{\inmversion}}
\begin{{document}}
\mainmatter
\input{{{note_rel}}}
\end{{document}}
"""


def run_direct_xelatex(
    root: Path, wrapper: Path, workdir: Path, verbose: bool
) -> subprocess.CompletedProcess[str]:
    """MiKTeX fallback for hosts without the Perl runtime required by latexmk.

    Preserve the original standalone wrapper and run XeLaTeX, Biber, then two
    XeLaTeX passes for independent bibliographies and stable cross-references.
    """
    base = wrapper.stem
    tex = ["xelatex", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={workdir}", str(wrapper)]
    bib = ["biber", f"--input-directory={workdir}", f"--output-directory={workdir}", base]
    for cmd in (tex, bib, tex, tex):
        try:
            result = subprocess.run(cmd, cwd=root, text=True, capture_output=not verbose)
        except OSError as error:
            return subprocess.CompletedProcess(cmd, 1, "", str(error))
        if result.returncode != 0:
            return result
    return result


def run_latexmk(root: Path, wrapper: Path, workdir: Path, verbose: bool) -> subprocess.CompletedProcess[str]:
    # latexmk.exe is a Perl wrapper on MiKTeX. The executable may exist even
    # though Perl is absent, yielding MiKTeX's unhelpful 'internal error'.
    if shutil.which("perl") is None:
        return run_direct_xelatex(root, wrapper, workdir, verbose)
    cmd = [
        "latexmk",
        "-xelatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-outdir={workdir}",
    ]
    if not verbose:
        cmd.append("-silent")
    cmd.append(str(wrapper))
    try:
        result = subprocess.run(cmd, cwd=root, text=True, capture_output=not verbose)
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        return result
    return run_direct_xelatex(root, wrapper, workdir, verbose)


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    notes_dir = (root / args.notes_dir).resolve()
    output_dir = (root / args.output_dir).resolve()
    build_dir = (root / args.build_dir).resolve()

    if not notes_dir.is_dir():
        print(f"Notes directory not found: {notes_dir}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    notes = sorted(notes_dir.glob("N-*.tex"))
    queue: list[NoteJob] = []
    for source in notes:
        note_id = source.stem
        pdf = output_dir / f"{note_id}.pdf"
        if needs_build(source, pdf, args.force):
            queue.append(NoteJob(note_id=note_id, source=source, pdf=pdf))

    if args.limit > 0:
        queue = queue[: args.limit]

    info(f"project root: {root}")
    info(f"notes found: {len(notes)}")
    info(f"to compile: {len(queue)}")
    info(f"output dir: {output_dir}")
    info(f"work dir: {build_dir}")

    if args.list_only:
        for job in queue:
            print(job.note_id)
        return 0

    if not queue:
        info("nothing to do")
        return 0

    successes: list[str] = []
    failures: list[str] = []

    def build_one(job: NoteJob) -> tuple[str, str | None]:
        note_id = job.note_id
        workdir = build_dir / note_id
        try:
            if workdir.exists():
                shutil.rmtree(workdir)
            workdir.mkdir(parents=True)
            wrapper = workdir / f"{note_id}-single.tex"
            wrapper.write_text(make_wrapper(note_id), encoding="utf-8")
            result = run_latexmk(root=root, wrapper=wrapper, workdir=workdir, verbose=args.verbose_latexmk)
            built_pdf = workdir / f"{note_id}-single.pdf"
            if result.returncode == 0 and built_pdf.exists():
                shutil.copy2(built_pdf, job.pdf)
                if args.clean_workdirs:
                    shutil.rmtree(workdir)
                return note_id, None
            details = (result.stdout or "")[-2000:] + (result.stderr or "")[-2000:]
            return note_id, f"exit={result.returncode}; log={workdir / (note_id + '-single.log')}; {details}"
        except Exception as exc:
            return note_id, f"{type(exc).__name__}: {exc}"

    max_workers = max(1, min(args.workers, 8))
    info(f"workers: {max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(build_one, job) for job in queue]
        for future in as_completed(futures):
            note_id, failure = future.result()
            if failure is None:
                successes.append(note_id)
                info(f"ok {note_id} -> {output_dir.name}/{note_id}.pdf")
            else:
                failures.append(note_id)
                print(f"[single-note] failed {note_id}: {failure}", file=sys.stderr, flush=True)

    print()
    info(f"compiled: {len(successes)}")
    info(f"failed: {len(failures)}")
    if failures:
        print("Failed notes:", file=sys.stderr)
        for note_id in failures:
            print(f"  {note_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

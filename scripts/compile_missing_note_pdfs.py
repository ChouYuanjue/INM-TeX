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


def run_latexmk(root: Path, wrapper: Path, workdir: Path, verbose: bool) -> subprocess.CompletedProcess[str]:
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

    if verbose:
        return subprocess.run(cmd, cwd=root, text=True)
    return subprocess.run(cmd, cwd=root, text=True, capture_output=True)


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

    for job in queue:
        note_id = job.note_id
        workdir = build_dir / note_id
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)

        wrapper = workdir / f"{note_id}-single.tex"
        wrapper.write_text(make_wrapper(note_id), encoding="utf-8")

        info(f"compiling {note_id}")
        result = run_latexmk(root=root, wrapper=wrapper, workdir=workdir, verbose=args.verbose_latexmk)
        built_pdf = workdir / f"{note_id}-single.pdf"

        if result.returncode == 0 and built_pdf.exists():
            shutil.copy2(built_pdf, job.pdf)
            successes.append(note_id)
            info(f"ok {note_id} -> {output_dir.name}/{note_id}.pdf")
            if args.clean_workdirs:
                shutil.rmtree(workdir)
        else:
            failures.append(note_id)
            print(f"[single-note] failed {note_id}; see {workdir}", file=sys.stderr)
            if not args.verbose_latexmk:
                log_path = workdir / f"{note_id}-single.log"
                if log_path.exists():
                    print(f"[single-note] log: {log_path}", file=sys.stderr)
                if result.stdout:
                    print(result.stdout[-4000:], file=sys.stderr)
                if result.stderr:
                    print(result.stderr[-4000:], file=sys.stderr)

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

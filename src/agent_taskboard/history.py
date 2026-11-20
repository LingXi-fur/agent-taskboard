"""Read-only per-task chronological history (``atb log``)."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, gitops
from .errors import AtbError
from .sheet import Sheet, extract_section, parse

_REWORK_LINE_RE = re.compile(r"^- \[([^\]]+)\]\s*(.*)$")


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=gitops.git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _format_local(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _parse_iso(text: str) -> datetime | None:
    raw = text.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _parse_commit_list(stdout: str) -> list[tuple[str, str, int, str]]:
    """Parse ``git log --follow --name-only --format=__COMMIT__%n%H%t%h%t%ct``."""
    entries: list[tuple[str, str, int, str]] = []
    blocks = stdout.split("__COMMIT__\n")
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        meta = lines[0].split("\t")
        if len(meta) != 3:
            continue
        full_hash, short_hash, ct_raw = meta
        try:
            commit_ts = int(ct_raw)
        except ValueError:
            continue
        file_path = lines[1].strip()
        entries.append((full_hash, short_hash, commit_ts, file_path))
    return entries


def _status_events(
    repo_root: Path, task_id: str
) -> tuple[list[dict[str, Any]], int, str | None, str | None]:
    rel_path = f".taskboard/tasks/{task_id}.md"
    proc = _run_git(
        [
            "log",
            "--follow",
            "--reverse",
            "--name-only",
            "--format=__COMMIT__%n%H%x09%h%x09%ct",
            "--",
            rel_path,
        ],
        repo_root,
    )
    if proc.returncode != 0:
        return [], 0, None, None

    events: list[dict[str, Any]] = []
    parse_errors = 0
    prev_status: str | None = None
    discovered_branch: str | None = None

    for full_hash, short_hash, commit_ts, file_path in _parse_commit_list(proc.stdout):
        show = _run_git(["show", f"{full_hash}:{file_path}"], repo_root)
        if show.returncode != 0:
            parse_errors += 1
            continue
        try:
            sheet = parse(show.stdout)
        except AtbError:
            parse_errors += 1
            continue

        if sheet.branch:
            discovered_branch = sheet.branch

        if prev_status is None:
            prev_status = sheet.status
            continue

        if sheet.status != prev_status:
            dt = datetime.fromtimestamp(commit_ts, tz=timezone.utc).astimezone()
            time_local = _format_local(dt)
            events.append({
                "kind": "status",
                "_sort_ts": float(commit_ts),
                "_order": 0,
                "timestamp": dt.isoformat(),
                "time_local": time_local,
                "commit": short_hash,
                "from": prev_status,
                "to": sheet.status,
                "text": f"{time_local}  {short_hash}  {prev_status} → {sheet.status}",
            })
            prev_status = sheet.status

    return events, parse_errors, discovered_branch, prev_status


def _test_events(
    repo_root: Path, task_id: str, *, verbose: bool
) -> list[dict[str, Any]]:
    path = repo_root / ".taskboard" / "runs" / f"{task_id}.jsonl"
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        dt = _parse_iso(str(rec.get("timestamp", "")))
        if dt is None:
            continue
        time_local = _format_local(dt)
        exit_code = rec.get("exit_code")
        duration = rec.get("duration")
        dur_text = (
            f"（{float(duration):.1f}s）"
            if isinstance(duration, (int, float))
            else ""
        )
        raw_output = str(rec.get("output", ""))
        tail = raw_output.rstrip("\n").splitlines()[-5:] if raw_output.strip() else []
        event: dict[str, Any] = {
            "kind": "test",
            "_sort_ts": dt.timestamp(),
            "_order": 1,
            "timestamp": dt.isoformat(),
            "time_local": time_local,
            "exit_code": exit_code,
            "duration": duration,
            "text": f"{time_local}  测试 exit {exit_code}{dur_text}",
        }
        if verbose:
            event["output_tail"] = tail
        events.append(event)
    return events


def _subject_mentions_merge(
    subject: str, task_id: str, branch: str | None
) -> bool:
    if branch:
        if re.search(
            rf"(?<![\w/.-]){re.escape(branch)}(?![\w/.-])", subject
        ):
            return True
    if re.search(rf"\({re.escape(task_id)}:", subject):
        return True
    return False


def _merge_events(
    repo_root: Path, cfg: config.Config, task_id: str, branch: str | None
) -> list[dict[str, Any]]:
    if not gitops.branch_exists(cfg.target, cwd=repo_root):
        return []

    branch_tip: str | None = None
    if branch and gitops.branch_exists(branch, cwd=repo_root):
        try:
            branch_tip = gitops.rev_parse(branch, cwd=repo_root)
        except gitops.GitError:
            branch_tip = None

    proc = _run_git(
        [
            "log",
            "--reverse",
            "--format=%H%x09%h%x09%ct%x09%P%x09%s",
            cfg.target,
        ],
        repo_root,
    )
    if proc.returncode != 0:
        return []

    events: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        full_hash, short_hash, ct_raw, parents_str, subject = parts
        if full_hash in seen_hashes:
            continue
        parents = parents_str.split()
        is_merge_commit = len(parents) >= 2
        matched_by_parent = (
            branch_tip is not None and is_merge_commit and branch_tip in parents[1:]
        )
        matched_by_msg = (
            (is_merge_commit or bool(re.match(r"(?i)^merge\b", subject)))
            and _subject_mentions_merge(subject, task_id, branch)
        )
        if not (matched_by_parent or matched_by_msg):
            continue
        try:
            commit_ts = int(ct_raw)
        except ValueError:
            continue
        seen_hashes.add(full_hash)
        dt = datetime.fromtimestamp(commit_ts, tz=timezone.utc).astimezone()
        time_local = _format_local(dt)
        events.append({
            "kind": "merge",
            "_sort_ts": float(commit_ts),
            "_order": 3,
            "timestamp": dt.isoformat(),
            "time_local": time_local,
            "commit": short_hash,
            "branch": branch,
            "text": f"{time_local}  已合并（{short_hash}）",
        })
    return events


def _rework_events(
    sheet: Sheet,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    section = extract_section(sheet.body, "## 返工")
    if not section:
        return [], []

    events: list[dict[str, Any]] = []
    notes: list[dict[str, str]] = []
    for line in section.splitlines():
        match = _REWORK_LINE_RE.match(line.strip())
        if not match:
            continue
        raw_ts, first_line = match.group(1), match.group(2).strip()
        dt = _parse_iso(raw_ts)
        time_local = _format_local(dt) if dt is not None else raw_ts
        sort_ts = dt.timestamp() if dt is not None else float("inf")
        notes.append({
            "timestamp": raw_ts,
            "time_local": time_local,
            "summary": first_line,
        })
        events.append({
            "kind": "rework",
            "_sort_ts": sort_ts,
            "_order": 2,
            "timestamp": dt.isoformat() if dt is not None else raw_ts,
            "time_local": time_local,
            "summary": first_line,
            "text": f"{time_local}  返工：{first_line}",
        })
    return events, notes


def rework_count(sheet: Sheet) -> int:
    """Number of rework notes recorded in the sheet body."""
    _events, notes = _rework_events(sheet)
    return len(notes)


def collect_task_history(
    repo_root: Path,
    cfg: config.Config,
    sheet: Sheet,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Collect chronological history events for ``sheet`` without writing anything."""
    status_evts, parse_errors, hist_branch, last_committed_status = _status_events(
        repo_root, sheet.id
    )
    if last_committed_status is not None and sheet.status != last_committed_status:
        sheet_path = repo_root / ".taskboard" / "tasks" / f"{sheet.id}.md"
        mtime = sheet_path.stat().st_mtime if sheet_path.exists() else datetime.now(timezone.utc).timestamp()
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone()
        time_local = _format_local(dt)
        status_evts.append({
            "kind": "status",
            "_sort_ts": float(mtime),
            "_order": 0,
            "timestamp": dt.isoformat(),
            "time_local": time_local,
            "commit": "uncommitted",
            "from": last_committed_status,
            "to": sheet.status,
            "text": f"{time_local}  uncommitted  {last_committed_status} → {sheet.status}",
        })
    branch = sheet.branch or hist_branch
    test_evts = _test_events(repo_root, sheet.id, verbose=verbose)
    rework_evts, rework_notes = _rework_events(sheet)
    merge_evts = _merge_events(repo_root, cfg, sheet.id, branch)

    combined = status_evts + test_evts + rework_evts + merge_evts
    combined.sort(key=lambda item: (item["_sort_ts"], item["_order"]))

    clean_events: list[dict[str, Any]] = []
    for item in combined:
        cleaned = {k: v for k, v in item.items() if not k.startswith("_")}
        clean_events.append(cleaned)

    return {
        "id": sheet.id,
        "title": sheet.title,
        "status": sheet.status,
        "branch": branch or "",
        "events": clean_events,
        "rework_count": len(rework_notes),
        "rework_notes": rework_notes,
        "parse_errors": parse_errors,
    }


def render_task_history(payload: dict[str, Any], *, verbose: bool = False) -> str:
    """Render chronological task history lines."""
    lines: list[str] = [
        f"{payload['id']} {payload['title']} [{payload['status']}]"
    ]
    events = payload["events"]
    if not events:
        lines.append("(无历史事件)")
    else:
        for event in events:
            lines.append(event["text"])
            if verbose and event["kind"] == "test":
                for out_line in event.get("output_tail", []):
                    lines.append(f"    {out_line}")

    lines.append(f"返工记录：{payload['rework_count']} 条")
    if payload["parse_errors"] > 0:
        lines.append(f"跳过无法解析的历史版本：{payload['parse_errors']} 个")
    return "\n".join(lines)

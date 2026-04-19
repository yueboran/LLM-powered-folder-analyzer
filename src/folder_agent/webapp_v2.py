from __future__ import annotations

import argparse
import json
import threading
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from folder_agent.cli import collect_analyses
from folder_agent.config import load_env_file
from folder_agent.llm import SiliconFlowAnalyzer
from folder_agent.report import build_report
from folder_agent.scanner import ScanStats, build_tree
from folder_agent.web_support_v3 import (
    build_snapshot,
    compare_snapshots,
    generate_cleanup_scripts,
    snapshot_metadata,
)


STATIC_DIR = Path(__file__).with_name("static")


@dataclass(slots=True)
class JobRecord:
    id: str
    status: str = "queued"
    progress: int = 0
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    snapshot_id: str | None = None


class AppState:
    def __init__(self, snapshot_dir: Path) -> None:
        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, JobRecord] = {}
        self.lock = threading.Lock()

    def create_job(self) -> JobRecord:
        job = JobRecord(id=uuid.uuid4().hex)
        with self.lock:
            self.jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.lock:
            return self.jobs.get(job_id)

    def append_log(self, job_id: str, message: str) -> None:
        with self.lock:
            self.jobs[job_id].logs.append(message)

    def update_job(self, job_id: str, **kwargs: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            for key, value in kwargs.items():
                setattr(job, key, value)

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        snapshot_path = self.snapshot_dir / f"{snapshot['id']}.json"
        markdown_path = self.snapshot_dir / f"{snapshot['id']}.md"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        markdown_path.write_text(snapshot["markdown"], encoding="utf-8-sig")

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = self.snapshot_dir / f"{snapshot_id}.json"
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def list_snapshots(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.snapshot_dir.glob("*.json"), reverse=True):
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            items.append(snapshot_metadata(snapshot))
        return items


def run_scan_job(state: AppState, job_id: str, payload: dict[str, Any]) -> None:
    try:
        load_env_file()
        root = Path(payload["rootPath"]).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Root path does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Root path is not a directory: {root}")

        state.update_job(job_id, status="running", progress=5)
        state.append_log(job_id, f"开始扫描: {root}")

        stats = ScanStats()
        root_node = build_tree(root, stats)
        state.update_job(job_id, progress=35)
        state.append_log(
            job_id,
            f"扫描完成: 直接子文件夹={len(root_node.children)} 不可访问={len(stats.inaccessible_paths)}",
        )

        top_n = int(payload.get("topN", 10))
        tree_max_chars = int(payload.get("treeMaxChars", 10000))
        skip_llm = bool(payload.get("skipLLM", False))

        analyses = []
        if skip_llm:
            state.append_log(job_id, "已跳过大模型分析。")
        else:
            state.append_log(job_id, f"开始生成 Top {top_n} 报告章节。")
            analyzer = SiliconFlowAnalyzer()

            def _job_log(message: str) -> None:
                state.append_log(job_id, message)

            analyses = collect_analyses(
                node=root_node,
                analyzer=analyzer,
                top_n=top_n,
                tree_max_chars=tree_max_chars,
                log_fn=_job_log,
            )

        state.update_job(job_id, progress=80)
        markdown = build_report(
            root=root_node,
            analyses=analyses,
            inaccessible_paths=stats.inaccessible_paths,
            top_n=top_n,
        )
        snapshot_id = uuid.uuid4().hex
        snapshot = build_snapshot(
            snapshot_id=snapshot_id,
            root_node=root_node,
            analyses=analyses,
            inaccessible_paths=stats.inaccessible_paths,
            top_n=top_n,
            options={"skipLLM": skip_llm, "treeMaxChars": tree_max_chars},
            markdown=markdown,
        )
        state.save_snapshot(snapshot)
        state.update_job(job_id, status="completed", progress=100, snapshot_id=snapshot_id)
        state.append_log(job_id, f"任务完成，快照已保存: {snapshot_id}")
    except Exception as exc:
        state.update_job(job_id, status="failed", error=str(exc))
        state.append_log(job_id, f"任务失败: {exc}")


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "FolderPanel/0.2"

    @property
    def app_state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_static("index_v2.html", "text/html; charset=utf-8")
        if parsed.path == "/app.js":
            return self._serve_static("app_v2.js", "application/javascript; charset=utf-8")
        if parsed.path == "/styles.css":
            return self._serve_static("styles_v2.css", "text/css; charset=utf-8")
        if parsed.path == "/api/health":
            return self._json_response({"ok": True})
        if parsed.path == "/api/snapshots":
            return self._json_response({"items": self.app_state.list_snapshots()})
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self.app_state.get_job(job_id)
            if job is None:
                return self._json_response({"error": "job not found"}, status=404)
            return self._json_response(
                {
                    "id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                    "logs": job.logs,
                    "error": job.error,
                    "snapshotId": job.snapshot_id,
                }
            )
        if parsed.path.startswith("/api/snapshots/") and parsed.path.endswith("/export"):
            parts = parsed.path.split("/")
            snapshot_id = parts[3]
            snapshot = self.app_state.load_snapshot(snapshot_id)
            export_format = (parse_qs(parsed.query).get("format") or ["markdown"])[0]
            if export_format == "json":
                data = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8-sig")
                return self._raw_response(data, "application/json; charset=utf-8")
            return self._raw_response(snapshot["markdown"].encode("utf-8-sig"), "text/markdown; charset=utf-8")
        if parsed.path.startswith("/api/snapshots/"):
            snapshot_id = parsed.path.rsplit("/", 1)[-1]
            return self._json_response(self.app_state.load_snapshot(snapshot_id))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        if parsed.path == "/api/jobs/scan":
            if not payload.get("rootPath"):
                return self._json_response({"error": "rootPath is required"}, status=400)
            job = self.app_state.create_job()
            thread = threading.Thread(target=run_scan_job, args=(self.app_state, job.id, payload), daemon=True)
            thread.start()
            return self._json_response({"jobId": job.id}, status=202)
        if parsed.path == "/api/snapshots/compare":
            if not payload.get("beforeId") or not payload.get("afterId"):
                return self._json_response({"error": "beforeId and afterId are required"}, status=400)
            before = self.app_state.load_snapshot(payload["beforeId"])
            after = self.app_state.load_snapshot(payload["afterId"])
            return self._json_response(compare_snapshots(before, after))
        if parsed.path == "/api/scripts/cleanup":
            if not payload.get("snapshotId"):
                return self._json_response({"error": "snapshotId is required"}, status=400)
            snapshot = self.app_state.load_snapshot(payload["snapshotId"])
            result = generate_cleanup_scripts(
                snapshot=snapshot,
                selected_paths=payload.get("selectedPaths", []),
                quarantine_root=payload.get("quarantineRoot") or str(self.app_state.snapshot_dir / "quarantine"),
                output_dir=self.app_state.snapshot_dir / "scripts",
            )
            return self._json_response(result)
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _serve_static(self, name: str, content_type: str) -> None:
        path = STATIC_DIR / name
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._raw_response(path.read_bytes(), content_type)

    def _json_response(self, payload: Any, status: int = 200) -> None:
        self._raw_response(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _raw_response(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local web panel for folder analysis.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default="report/panel_data")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = AppState(Path(args.data_dir).expanduser().resolve())
    server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    server.app_state = state  # type: ignore[attr-defined]
    print(f"Folder panel running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

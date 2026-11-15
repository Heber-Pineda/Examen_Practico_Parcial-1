"""Aceptación manual con proveedor real; listar casos no realiza conexiones."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def http_error_details(error):
    """Conserva estado/código conocidos, sin cuerpo, URL, cabeceras ni mensaje crudo."""
    details = {"error_type": "HTTPError", "http_status": error.code}
    try:
        code = json.loads(error.read(8192))["error"]["code"]
        if code in {"AI_NOT_CONFIGURED", "AI_RATE_LIMIT", "AI_BUSY", "AI_PROVIDER_AUTH", "AI_PROVIDER_LIMIT",
                    "AI_PROVIDER_UNAVAILABLE", "AI_PROVIDER_REDIRECT", "AI_PROVIDER_TIMEOUT", "AI_MODEL_NOT_AVAILABLE",
                    "AI_PROVIDER_INVALID_RESPONSE", "AI_UNVERIFIED_ANSWER", "DW_UNAVAILABLE", "DW_BUSY",
                    "PUBLICATION_CHANGED", "UNAUTHORIZED", "INVALID_REQUEST", "INVALID_FILTERS", "INVALID_QUESTION"}:
            details["error_code"] = code
    except (ValueError, KeyError, TypeError, OSError, AttributeError, RecursionError):
        pass
    return details


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("cases", "acceptance"))
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-http-access", action="store_true")
    args = parser.parse_args(argv)
    cases = json.loads(Path(__file__).with_name("acceptance_cases.json").read_text(encoding="utf-8"))
    if args.command == "cases":
        print(json.dumps(cases, ensure_ascii=False, indent=2))
        return 0
    origin = urlparse(args.api_url)
    token = os.environ.get("API_ACCESS_TOKEN", "")
    if not args.allow_http_access or not token or origin.hostname not in {"127.0.0.1", "localhost"} or origin.scheme != "http" or origin.username or origin.password:
        print("Requiere --allow-http-access, API_ACCESS_TOKEN y API local http.", file=sys.stderr)
        return 2
    base = args.api_url.rstrip("/")
    def call(path, body=None, timeout=100):
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    try:
        status = call("/api/v1/assistant/status")
        if status["status"] != "READY":
            print("PENDIENTE: configurar el proveedor real; no se ejecutaron las diez preguntas.")
            return 2
        budget = status.get("request_timeout_seconds", 100)
        timeout = min(300, max(100, budget)) if type(budget) in (int, float) else 100
        results = []
        for case in cases:
            try:
                try:
                    answer = call("/api/v1/assistant/ask", {"question": case["question"]}, timeout)
                except HTTPError as exc:
                    if exc.code != 429:
                        raise
                    time.sleep(60)
                    answer = call("/api/v1/assistant/ask", {"question": case["question"]}, timeout)
            except Exception as exc:
                details = http_error_details(exc) if isinstance(exc, HTTPError) else {"error_type": type(exc).__name__}
                results.append({**case, "result": "FAIL", "obtained": details, "unverified_assertions": []})
                continue
            facts = {item["statement"] for item in answer.get("evidence", [])}
            unverified = [text for text in answer.get("paragraphs", []) if text not in facts]
            passed = answer.get("status") == case["expected_status"] and not unverified
            if case["expected_query"]:
                passed = passed and bool(answer.get("paragraphs")) and any(item["query_id"] == case["expected_query"] for item in answer.get("evidence", []))
            results.append({**case, "result": "PASS" if passed else "FAIL", "obtained": answer, "unverified_assertions": unverified})
        directory = Path(__file__).resolve().parents[1] / "artifacts" / "genai-acceptance"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        file = directory / (stamp + ".json")
        file.write_text(json.dumps({"executed_at_utc": stamp, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"evidence_file": str(file), "passed": sum(r["result"] == "PASS" for r in results), "total": len(results)}))
        return 0 if all(r["result"] == "PASS" for r in results) else 1
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "message": "No se completó la aceptación real. Verificar API, proveedor y cuota."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

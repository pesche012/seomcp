from __future__ import annotations

import base64
import argparse
import html
import json
import os
import re
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


JSON = dict[str, Any]


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class Config:
    wp_base_url: str
    wp_username: str
    wp_application_password: str
    wp_timeout_seconds: float
    gsc_access_token: str
    gsc_site_url: str
    ga4_access_token: str
    ga4_property_id: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            wp_base_url=os.getenv("WP_BASE_URL", "").rstrip("/"),
            wp_username=os.getenv("WP_USERNAME", ""),
            wp_application_password=os.getenv("WP_APPLICATION_PASSWORD", ""),
            wp_timeout_seconds=float(os.getenv("WP_TIMEOUT_SECONDS", "20")),
            gsc_access_token=os.getenv("GSC_ACCESS_TOKEN", ""),
            gsc_site_url=os.getenv("GSC_SITE_URL", ""),
            ga4_access_token=os.getenv("GA4_ACCESS_TOKEN", ""),
            ga4_property_id=os.getenv("GA4_PROPERTY_ID", ""),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", ""),
        )


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def ensure_positive_int(value: Any, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise McpError(-32602, f"Expected integer, got {value!r}") from exc
    if parsed < 1:
        raise McpError(-32602, "Integer value must be >= 1")
    return min(parsed, maximum)


class HttpClient:
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return self._request_json("GET", url, headers=headers)

    def post_json(self, url: str, payload: JSON, headers: dict[str, str] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        return self._request_json("POST", url, data=body, headers=request_headers)

    def post_form(self, url: str, payload: dict[str, str], headers: dict[str, str] | None = None) -> Any:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        return self._request_json("POST", url, data=body, headers=request_headers)

    def _request_json(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise McpError(-32000, f"HTTP {method} failed for {url}: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpError(-32000, f"Response was not JSON for {url}") from exc


class SeoDataReader:
    def __init__(self, config: Config, http_client: HttpClient):
        self.config = config
        self.http = http_client
        self._google_access_token: str | None = None
        self._google_access_token_expires_at = 0.0

    def wp_headers(self) -> dict[str, str]:
        if not self.config.wp_username or not self.config.wp_application_password:
            return {}
        token = f"{self.config.wp_username}:{self.config.wp_application_password}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    def require_wp(self) -> None:
        if not self.config.wp_base_url:
            raise McpError(-32001, "WP_BASE_URL is required")

    def require_gsc(self) -> None:
        if not self.google_access_token("GSC_ACCESS_TOKEN"):
            raise McpError(-32001, "GSC_ACCESS_TOKEN or GOOGLE_REFRESH_TOKEN is required")

    def require_ga4(self) -> None:
        if not self.google_access_token("GA4_ACCESS_TOKEN"):
            raise McpError(-32001, "GA4_ACCESS_TOKEN or GOOGLE_REFRESH_TOKEN is required")
        if not self.config.ga4_property_id:
            raise McpError(-32001, "GA4_PROPERTY_ID is required")

    def wp_url(self, path: str, query: JSON | None = None) -> str:
        self.require_wp()
        encoded = urllib.parse.urlencode(query or {}, doseq=True)
        suffix = f"?{encoded}" if encoded else ""
        return f"{self.config.wp_base_url}/wp-json/wp/v2/{path.lstrip('/')}{suffix}"

    def gsc_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.google_access_token('GSC_ACCESS_TOKEN')}"}

    def ga4_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.google_access_token('GA4_ACCESS_TOKEN')}"}

    def google_access_token(self, token_name: str) -> str:
        static_token = self.config.gsc_access_token if token_name == "GSC_ACCESS_TOKEN" else self.config.ga4_access_token
        if static_token:
            return static_token
        if self._google_access_token and time.time() < self._google_access_token_expires_at - 60:
            return self._google_access_token
        if not self.config.google_client_id or not self.config.google_client_secret or not self.config.google_refresh_token:
            return ""
        response = self.http.post_form(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": self.config.google_client_id,
                "client_secret": self.config.google_client_secret,
                "refresh_token": self.config.google_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        access_token = response.get("access_token") if isinstance(response, dict) else None
        if not access_token:
            raise McpError(-32001, "Google OAuth refresh did not return an access_token")
        expires_in = response.get("expires_in", 3600)
        try:
            expires_seconds = int(expires_in)
        except (TypeError, ValueError):
            expires_seconds = 3600
        self._google_access_token = str(access_token)
        self._google_access_token_expires_at = time.time() + expires_seconds
        return self._google_access_token

    def wp_list_posts(self, args: JSON) -> Any:
        per_page = ensure_positive_int(args.get("per_page"), 10, 100)
        page = ensure_positive_int(args.get("page"), 1, 10000)
        status = args.get("status", "publish")
        if status not in {"publish", "future", "draft", "pending", "private", "any"}:
            raise McpError(-32602, "Invalid WordPress post status")
        query: JSON = {
            "per_page": per_page,
            "page": page,
            "status": status,
            "_fields": args.get(
                "fields",
                "id,date,modified,slug,link,title,excerpt,categories,tags,status",
            ),
        }
        if args.get("search"):
            query["search"] = str(args["search"])
        return self.http.get_json(self.wp_url("posts", query), headers=self.wp_headers())

    def wp_get_post(self, args: JSON) -> Any:
        post_id = args.get("id")
        if not post_id:
            raise McpError(-32602, "id is required")
        context = args.get("context", "view")
        fields = args.get(
            "fields",
            "id,date,modified,slug,link,title,content,excerpt,categories,tags,status",
        )
        return self.http.get_json(
            self.wp_url(f"posts/{int(post_id)}", {"context": context, "_fields": fields}),
            headers=self.wp_headers(),
        )

    def wp_search_posts(self, args: JSON) -> Any:
        if not args.get("query"):
            raise McpError(-32602, "query is required")
        return self.wp_list_posts({"search": args["query"], "per_page": args.get("per_page", 10)})

    def gsc_list_sites(self, args: JSON) -> Any:
        self.require_gsc()
        return self.http.get_json(
            "https://www.googleapis.com/webmasters/v3/sites",
            headers=self.gsc_headers(),
        )

    def gsc_search_analytics(self, args: JSON, dimensions: list[str]) -> Any:
        self.require_gsc()
        site_url = args.get("site_url") or self.config.gsc_site_url
        if not site_url:
            raise McpError(-32602, "site_url or GSC_SITE_URL is required")
        payload: JSON = {
            "startDate": args.get("start_date"),
            "endDate": args.get("end_date"),
            "dimensions": dimensions,
            "rowLimit": ensure_positive_int(args.get("row_limit"), 100, 25000),
        }
        if not payload["startDate"] or not payload["endDate"]:
            raise McpError(-32602, "start_date and end_date are required")
        filters = args.get("filters")
        if filters:
            payload["dimensionFilterGroups"] = [{"filters": filters}]
        encoded_site = urllib.parse.quote(str(site_url), safe="")
        url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
        return self.http.post_json(url, payload, headers=self.gsc_headers())

    def gsc_get_search_performance(self, args: JSON) -> Any:
        dimensions = args.get("dimensions") or ["page"]
        if not isinstance(dimensions, list):
            raise McpError(-32602, "dimensions must be a list")
        return self.gsc_search_analytics(args, [str(item) for item in dimensions])

    def gsc_get_page_queries(self, args: JSON) -> Any:
        page_url = args.get("page_url")
        if not page_url:
            raise McpError(-32602, "page_url is required")
        merged = {
            **args,
            "filters": [
                {
                    "dimension": "page",
                    "operator": "equals",
                    "expression": page_url,
                }
            ],
        }
        return self.gsc_search_analytics(merged, ["query"])

    def gsc_get_query_pages(self, args: JSON) -> Any:
        query = args.get("query")
        if not query:
            raise McpError(-32602, "query is required")
        merged = {
            **args,
            "filters": [
                {
                    "dimension": "query",
                    "operator": "equals",
                    "expression": query,
                }
            ],
        }
        return self.gsc_search_analytics(merged, ["page"])

    def ga4_run_report(self, args: JSON, dimensions: list[str], metrics: list[str]) -> Any:
        self.require_ga4()
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        if not start_date or not end_date:
            raise McpError(-32602, "start_date and end_date are required")
        payload: JSON = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": name} for name in dimensions],
            "metrics": [{"name": name} for name in metrics],
            "limit": str(ensure_positive_int(args.get("limit"), 100, 100000)),
        }
        if args.get("dimension_filter"):
            payload["dimensionFilter"] = args["dimension_filter"]
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{self.config.ga4_property_id}:runReport"
        return self.http.post_json(url, payload, headers=self.ga4_headers())

    def ga4_get_page_metrics(self, args: JSON) -> Any:
        metrics = args.get("metrics") or ["screenPageViews", "sessions", "totalUsers", "averageSessionDuration"]
        dimensions = args.get("dimensions") or ["pagePathPlusQueryString", "pageTitle"]
        return self.ga4_run_report(args, dimensions, metrics)

    def ga4_get_traffic_sources(self, args: JSON) -> Any:
        metrics = args.get("metrics") or ["sessions", "totalUsers", "screenPageViews"]
        return self.ga4_run_report(args, ["sessionSource", "sessionMedium"], metrics)

    def ga4_get_landing_pages(self, args: JSON) -> Any:
        metrics = args.get("metrics") or ["sessions", "totalUsers", "screenPageViews", "engagedSessions"]
        return self.ga4_run_report(args, ["landingPagePlusQueryString"], metrics)

    def seo_get_article_snapshot(self, args: JSON) -> Any:
        post = self.wp_get_post(args)
        content = post.get("content", {}).get("rendered", "") if isinstance(post, dict) else ""
        return {
            "wordpress": post,
            "content_plaintext": strip_html(content),
        }

    def seo_get_article_performance(self, args: JSON) -> Any:
        post = self.wp_get_post(args)
        page_url = args.get("page_url")
        if not page_url and isinstance(post, dict):
            page_url = post.get("link")
        result: JSON = {"wordpress": post}
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        if page_url and start_date and end_date and self.google_access_token("GSC_ACCESS_TOKEN"):
            result["search_console"] = self.gsc_get_page_queries(
                {
                    "page_url": page_url,
                    "start_date": start_date,
                    "end_date": end_date,
                    "row_limit": args.get("row_limit", 100),
                    "site_url": args.get("site_url"),
                }
            )
        if page_url and start_date and end_date and self.config.ga4_property_id and self.google_access_token("GA4_ACCESS_TOKEN"):
            parsed = urllib.parse.urlparse(str(page_url))
            page_path = parsed.path or "/"
            if parsed.query:
                page_path = f"{page_path}?{parsed.query}"
            result["ga4"] = self.ga4_get_page_metrics(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": args.get("limit", 100),
                    "dimension_filter": {
                        "filter": {
                            "fieldName": "pagePathPlusQueryString",
                            "stringFilter": {"matchType": "EXACT", "value": page_path},
                        }
                    },
                }
            )
        return result


TOOL_SCHEMAS: list[JSON] = [
    {
        "name": "wp.list_posts",
        "description": "Read WordPress posts with pagination. GET-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
                "page": {"type": "integer", "minimum": 1},
                "status": {"type": "string"},
                "search": {"type": "string"},
                "fields": {"type": "string"},
            },
        },
    },
    {
        "name": "wp.get_post",
        "description": "Read a single WordPress post by id. GET-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "context": {"type": "string"}, "fields": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "wp.search_posts",
        "description": "Search WordPress posts. GET-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "per_page": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "gsc.list_sites",
        "description": "List Search Console sites using read-only OAuth credentials.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "gsc.get_search_performance",
        "description": "Read Search Console search analytics rows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "row_limit": {"type": "integer"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "gsc.get_page_queries",
        "description": "Read Search Console queries for one page URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "page_url": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "row_limit": {"type": "integer"},
            },
            "required": ["page_url", "start_date", "end_date"],
        },
    },
    {
        "name": "gsc.get_query_pages",
        "description": "Read Search Console pages for one query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "query": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "row_limit": {"type": "integer"},
            },
            "required": ["query", "start_date", "end_date"],
        },
    },
    {
        "name": "ga4.get_page_metrics",
        "description": "Read GA4 page metrics using the Data API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "dimension_filter": {"type": "object"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "ga4.get_traffic_sources",
        "description": "Read GA4 traffic sources.",
        "inputSchema": {
            "type": "object",
            "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "ga4.get_landing_pages",
        "description": "Read GA4 landing page metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "seo.get_article_snapshot",
        "description": "Read a WordPress post and return HTML plus extracted plain text.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    },
    {
        "name": "seo.get_article_performance",
        "description": "Read WordPress post data plus optional GSC and GA4 performance for the same URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "page_url": {"type": "string"},
                "site_url": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "row_limit": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
]


class McpServer:
    def __init__(self, reader: SeoDataReader):
        self.reader = reader
        self.handlers: dict[str, Callable[[JSON], Any]] = {
            "wp.list_posts": reader.wp_list_posts,
            "wp.get_post": reader.wp_get_post,
            "wp.search_posts": reader.wp_search_posts,
            "gsc.list_sites": reader.gsc_list_sites,
            "gsc.get_search_performance": reader.gsc_get_search_performance,
            "gsc.get_page_queries": reader.gsc_get_page_queries,
            "gsc.get_query_pages": reader.gsc_get_query_pages,
            "ga4.get_page_metrics": reader.ga4_get_page_metrics,
            "ga4.get_traffic_sources": reader.ga4_get_traffic_sources,
            "ga4.get_landing_pages": reader.ga4_get_landing_pages,
            "seo.get_article_snapshot": reader.seo_get_article_snapshot,
            "seo.get_article_performance": reader.seo_get_article_performance,
        }

    def handle(self, request: JSON) -> JSON | None:
        method = request.get("method")
        request_id = request.get("id")
        try:
            if method == "initialize":
                return self.result(
                    request_id,
                    {
                        "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "seo-data-reader-mcp", "version": "0.1.0"},
                    },
                )
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return self.result(request_id, {"tools": TOOL_SCHEMAS})
            if method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                if name not in self.handlers:
                    raise McpError(-32601, f"Unknown tool: {name}")
                data = self.handlers[name](args)
                return self.result(request_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]})
            raise McpError(-32601, f"Unknown method: {method}")
        except McpError as exc:
            return self.error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            return self.error(request_id, -32603, str(exc))

    @staticmethod
    def result(request_id: Any, value: Any) -> JSON:
        return {"jsonrpc": "2.0", "id": request_id, "result": value}

    @staticmethod
    def error(request_id: Any, code: int, message: str, data: Any | None = None) -> JSON:
        error: JSON = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def build_server() -> McpServer:
    config = Config.from_env()
    return McpServer(SeoDataReader(config, HttpClient(config.wp_timeout_seconds)))


def run_stdio() -> None:
    server = build_server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = McpServer.error(None, -32700, "Parse error")
        else:
            response = server.handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = "SeoDataReaderMcp/0.1"

    @property
    def mcp_server(self) -> McpServer:
        return self.server.mcp_server  # type: ignore[attr-defined]

    @property
    def auth_token(self) -> str:
        return self.server.auth_token  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/", "/health"}:
            self.write_json({"status": "ok", "mcp": "/mcp", "sse": "/sse"})
            return
        if path == "/mcp":
            self.write_sse_headers()
            self.wfile.write(b": seo-data-reader-mcp ready\n\n")
            self.wfile.flush()
            return
        if path == "/sse":
            self.write_sse_headers()
            self.wfile.write(b"event: endpoint\n")
            self.wfile.write(b"data: /messages\n\n")
            self.wfile.flush()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path not in {"/mcp", "/messages"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.is_authorized():
            self.send_error(HTTPStatus.UNAUTHORIZED)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self.write_json(McpServer.error(None, -32700, "Parse error"), HTTPStatus.BAD_REQUEST)
            return
        response = self.mcp_server.handle(request)
        if response is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.end_headers()
            return
        self.write_json(response)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization, MCP-Protocol-Version, Mcp-Session-Id")
        self.end_headers()

    def is_authorized(self) -> bool:
        if not self.auth_token:
            return True
        return self.headers.get("Authorization") == f"Bearer {self.auth_token}"

    def allowed_origin(self) -> str:
        configured = os.getenv("MCP_ALLOWED_ORIGIN", "")
        return configured or self.headers.get("Origin") or "*"

    def write_json(self, payload: JSON, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.end_headers()
        self.wfile.write(body)

    def write_sse_headers(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))


def run_http(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), McpHttpHandler)
    httpd.mcp_server = build_server()  # type: ignore[attr-defined]
    httpd.auth_token = os.getenv("MCP_HTTP_TOKEN", "")  # type: ignore[attr-defined]
    print(f"seo-data-reader-mcp listening on http://{host}:{port}/mcp", file=sys.stderr)
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only SEO MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.getenv("MCP_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.getenv("MCP_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_HTTP_PORT", "8765")))
    args = parser.parse_args()
    if args.transport == "http":
        run_http(args.host, args.port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()

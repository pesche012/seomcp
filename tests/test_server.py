import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from seo_data_reader_mcp.server import Config, HttpClient, McpHttpHandler, McpServer, SeoDataReader, strip_html


class FakeHttp(HttpClient):
    def __init__(self):
        super().__init__(timeout_seconds=1)
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append(("GET", url, headers, None))
        if "/posts/7" in url:
            return {
                "id": 7,
                "link": "https://example.com/post-7/",
                "title": {"rendered": "Title"},
                "content": {"rendered": "<p>Hello <strong>SEO</strong></p>"},
            }
        return [{"id": 7, "link": "https://example.com/post-7/"}]

    def post_json(self, url, payload, headers=None):
        self.calls.append(("POST", url, headers, payload))
        return {"rows": [{"keys": ["seo"], "clicks": 1}]}


def make_server():
    config = Config(
        wp_base_url="https://example.com",
        wp_username="",
        wp_application_password="",
        wp_timeout_seconds=1,
        gsc_access_token="token",
        gsc_site_url="https://example.com/",
        ga4_access_token="token",
        ga4_property_id="123",
        google_client_id="",
        google_client_secret="",
        google_refresh_token="",
    )
    fake_http = FakeHttp()
    return McpServer(SeoDataReader(config, fake_http)), fake_http


class ServerTest(unittest.TestCase):
    def test_tools_list(self):
        server, _ = make_server()
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("wp.get_post", names)
        self.assertIn("seo.get_article_performance", names)

    def test_snapshot_strips_html(self):
        server, _ = make_server()
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "seo.get_article_snapshot", "arguments": {"id": 7}},
            }
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["content_plaintext"], "Hello SEO")

    def test_gsc_page_query_is_read_endpoint(self):
        server, fake_http = make_server()
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "gsc.get_page_queries",
                    "arguments": {
                        "page_url": "https://example.com/post-7/",
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                    },
                },
            }
        )
        method, url, _, payload = fake_http.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("searchAnalytics/query", url)
        self.assertEqual(payload["dimensions"], ["query"])

    def test_strip_html_removes_script(self):
        self.assertEqual(strip_html("<script>x</script><p>A&nbsp;B</p>"), "A B")

    def test_refresh_token_is_exchanged_for_google_access_token(self):
        config = Config(
            wp_base_url="https://example.com",
            wp_username="",
            wp_application_password="",
            wp_timeout_seconds=1,
            gsc_access_token="",
            gsc_site_url="https://example.com/",
            ga4_access_token="",
            ga4_property_id="123",
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_refresh_token="refresh-token",
        )
        fake_http = FakeHttp()
        fake_http.post_form = lambda url, payload, headers=None: {"access_token": "fresh-token", "expires_in": 3600}
        reader = SeoDataReader(config, fake_http)

        self.assertEqual(reader.gsc_headers(), {"Authorization": "Bearer fresh-token"})
        self.assertEqual(reader.ga4_headers(), {"Authorization": "Bearer fresh-token"})

    def test_http_transport_handles_tools_list(self):
        server, _ = make_server()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), McpHttpHandler)
        httpd.mcp_server = server
        httpd.auth_token = ""
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            body = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/list"}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/mcp",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            names = [tool["name"] for tool in payload["result"]["tools"]]
            self.assertIn("wp.list_posts", names)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

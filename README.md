# SEOAnalytics Read MCP

WordPress、Google Search Console、Google Analytics 4 のSEO分析用データを取得するための、読み取り専用MCPサーバーです。

このサーバーは事故防止を優先して、取得だけに機能を限定しています。記事の作成、更新、削除、公開、メディアアップロード、GA4設定変更、Search Console設定変更はできません。

## できること

- WordPressの記事一覧を取得する
- WordPressの記事本文、タイトル、URL、カテゴリ、タグ、更新日を取得する
- Search Consoleのクリック数、表示回数、CTR、平均掲載順位を取得する
- ページごとの検索クエリを取得する
- クエリごとの流入ページを取得する
- GA4のページビュー、セッション、ユーザー、流入元、ランディングページを取得する
- WordPress記事とGSC/GA4の数値をまとめて取得する

## できないこと

安全のため、以下は実装していません。

- WordPress記事の作成
- WordPress記事の更新
- WordPress記事の削除
- 下書き作成
- 公開処理
- タグ、カテゴリ変更
- メディアアップロード
- GA4の設定変更
- Search Consoleのサイトマップ送信や削除

## MCPツール一覧

```text
wp.list_posts
wp.get_post
wp.search_posts

gsc.list_sites
gsc.get_search_performance
gsc.get_page_queries
gsc.get_query_pages

ga4.get_page_metrics
ga4.get_traffic_sources
ga4.get_landing_pages

seo.get_article_snapshot
seo.get_article_performance
```

## フォルダ構成

```text
SEOAnalytics/
  README.md
  Dockerfile
  docker-compose.yml
  pyproject.toml
  .env.example
  src/
    seo_data_reader_mcp/
      server.py
  tests/
  work/
  outputs/
```

## 1. 最初に.envを作る

`.env.example` をコピーして `.env` を作ります。

Windows PowerShellの場合:

```powershell
Copy-Item .env.example .env
```

Ubuntu / WSLの場合:

```bash
cp .env.example .env
```

そのあと `.env` を編集します。

## 2. WordPressの設定

公開記事だけを読む場合は、まずこれだけで試せます。

```text
WP_BASE_URL=https://example.com
```

例:

```text
WP_BASE_URL=https://your-wordpress-site.example
```

WordPress REST APIのURLは、内部的には次のようになります。

```text
https://example.com/wp-json/wp/v2/posts
```

非公開記事や認証が必要な情報も読みたい場合だけ、WordPressのアプリケーションパスワードを使います。

```text
WP_USERNAME=reader@example.com
WP_APPLICATION_PASSWORD=
```

WordPress側の準備:

1. WordPress管理画面にログインします。
2. 読み取り専用に近い権限のユーザーを用意します。
3. ユーザーのプロフィール画面を開きます。
4. 「アプリケーションパスワード」を発行します。
5. 発行されたパスワードを `WP_APPLICATION_PASSWORD` に入れます。

注意: WordPress本体には完全な「REST API読み取り専用トークン」が標準で用意されているわけではありません。事故を避けるため、このMCP側ではGETリクエストしか実行しない作りにしています。

## 3. Google OAuthの長期設定

OpenCrawlなどのMCPクライアントで長期的に使う場合は、短命のアクセストークンではなく、Google OAuthの `refresh_token` を使うのがおすすめです。

`.env` には次の3つを入れます。

```text
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
```

このMCPサーバーは、`GOOGLE_REFRESH_TOKEN` から新しいアクセストークンを自動取得して、Search Console APIとGA4 Data APIを読みます。

Google Cloud Consoleで有効化するAPI:

```text
Google Search Console API
Google Analytics Data API
```

OAuth同意で許可するスコープ:

```text
https://www.googleapis.com/auth/webmasters.readonly
https://www.googleapis.com/auth/analytics.readonly
```

アクセストークンはGoogle側で期限切れになりますが、`refresh_token` があればMCPサーバー側で自動更新します。

### refresh tokenの取り方

1. Google Cloud Consoleでプロジェクトを作成します。
2. 「APIとサービス」から `Google Search Console API` と `Google Analytics Data API` を有効化します。
3. OAuth同意画面を設定します。
4. 「認証情報」からOAuthクライアントIDを作成します。
5. OAuth Playgroundなどで上記2つのスコープを許可し、`refresh_token` を取得します。

OAuth Playgroundを使う場合は、右上の歯車から `Use your own OAuth credentials` を有効にし、Google Cloud Consoleで作成した `GOOGLE_CLIENT_ID` と `GOOGLE_CLIENT_SECRET` を使ってください。

注意: `GOOGLE_CLIENT_SECRET` と `GOOGLE_REFRESH_TOKEN` は秘密情報です。Gitにコミットしたり、チャットにそのまま貼ったりしないでください。

`.env` の完成例:

```env
WP_BASE_URL=https://your-wordpress-site.example

GSC_SITE_URL=https://your-wordpress-site.example/
GA4_PROPERTY_ID=123456789

GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=

GSC_ACCESS_TOKEN=
GA4_ACCESS_TOKEN=
```

## 4. Search Consoleの設定

Search Consoleを使う場合は、Search Consoleに登録されているプロパティ表記を `.env` に入れます。

```text
GSC_SITE_URL=https://example.com/
```

`GSC_SITE_URL` はSearch Consoleに登録されているプロパティ表記と合わせてください。

URLプレフィックスプロパティの例:

```text
GSC_SITE_URL=https://example.com/
```

ドメインプロパティの例:

```text
GSC_SITE_URL=sc-domain:example.com
```

Search Consoleで使える主なツール:

```text
gsc.list_sites
gsc.get_search_performance
gsc.get_page_queries
gsc.get_query_pages
```

一時的な接続テストだけなら、短命のOAuthアクセストークンを直接入れることもできます。

```text
GSC_ACCESS_TOKEN=
```

ただし、これは期限切れになるため長期運用には向きません。

## 5. GA4の設定

GA4を使う場合は、GA4 Data API用のプロパティIDを `.env` に入れます。

```text
GA4_PROPERTY_ID=123456789
```

GA4プロパティIDは、GA4管理画面のプロパティ詳細で確認できます。`G-XXXXXXXXXX` の測定IDではなく、数字だけのプロパティIDを使います。

GA4で使える主なツール:

```text
ga4.get_page_metrics
ga4.get_traffic_sources
ga4.get_landing_pages
```

一時的な接続テストだけなら、短命のOAuthアクセストークンを直接入れることもできます。

```text
GA4_ACCESS_TOKEN=
```

ただし、これは期限切れになるため長期運用には向きません。

## 6. Dockerで起動する

このプロジェクトのフォルダで実行します。

```powershell
cd <PROJECT_DIR>
```

ビルド:

```powershell
docker compose build seo-data-reader-mcp
```

MCPサーバーを起動:

```powershell
docker compose run --rm -T seo-data-reader-mcp
```

MCPはstdioで通信するため、通常のWebサーバーのようにポート番号は出ません。OpenCrawlなどのMCPクライアントから起動コマンドとして呼び出します。

HTTPで常時稼働させる場合:

```powershell
docker compose up -d seo-data-reader-mcp-http
```

HTTP版は次のURLで待ち受けます。

```text
http://localhost:8765/mcp
http://<LAN_HOST>:8765/mcp
```

Web UIが古いHTTP+SSE形式を要求する場合は、次も使えます。

```text
http://localhost:8765/sse
http://<LAN_HOST>:8765/sse
```

稼働確認:

```powershell
docker compose ps
```

停止:

```powershell
docker compose down
```

## 7. OpenCrawlへの設定例

Dockerで使う場合の例です。

```json
{
  "mcpServers": {
    "seo-data-reader": {
      "command": "docker",
      "args": [
        "compose",
        "-f",
        "<PROJECT_DIR>\\docker-compose.yml",
        "run",
        "--rm",
        "-T",
        "seo-data-reader-mcp"
      ],
      "env": {}
    }
  }
}
```

OpenCrawlからWSL経由でDockerを呼びたい場合の例です。

```json
{
  "mcpServers": {
    "seo-data-reader": {
      "command": "wsl",
      "args": [
        "-e",
        "bash",
        "-lc",
        "cd '/path/to/SEOAnalytics' && docker compose run --rm -T seo-data-reader-mcp"
      ],
      "env": {}
    }
  }
}
```

Docker Composeは `.env` を読むため、通常はOpenCrawl側の `env` にGoogle認証情報を直接書かなくて大丈夫です。`.env` は `docker-compose.yml` と同じフォルダに置いてください。

Ubuntuサーバー上で、Dockerを使わずPythonで直接動かす場合の例です。

```json
{
  "mcpServers": {
    "seo-data-reader": {
      "command": "python3",
      "args": ["-m", "seo_data_reader_mcp.server"],
      "cwd": "/path/to/SEOAnalytics",
      "env": {
        "PYTHONPATH": "/path/to/SEOAnalytics/src"
      }
    }
  }
}
```

UbuntuサーバーでもDockerを使う場合は、サーバー上の配置パスに合わせて `docker-compose.yml` の場所を変えてください。

## 8. ヘルメスエージェントへの設定例

ヘルメスエージェントからstdio起動する場合は、同梱の起動スクリプトをMCPサーバーとして登録してください。

設定例は `hermes-agent.mcp.example.json` に入っています。

```json
{
  "mcpServers": {
    "seo-analytics": {
      "command": "powershell",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "<PROJECT_DIR>\\scripts\\hermes-start.ps1"
      ],
      "env": {}
    }
  }
}
```

ヘルメスエージェント側にMCPサーバー設定画面、またはMCP設定JSONがある場合は、この `seo-analytics` の設定を追加します。

`scripts/hermes-start.ps1` はプロジェクトフォルダへ移動してから次を実行します。

```powershell
docker compose run --rm -T seo-data-reader-mcp
```

そのため、ヘルメスエージェント側が `cwd` を指定できない場合でも起動できます。`.env` はこれまで通り `SEOAnalytics` フォルダ直下に置いてください。

常時稼働しているHTTPサーバーへWeb UIから接続する場合は、`hermes-agent.http.example.json` の形式を使います。

```json
{
  "mcpServers": {
    "seo-analytics": {
      "url": "http://localhost:8765/mcp",
      "transport": "streamable-http"
    }
  }
}
```

別PCやコンテナ上のWeb UIから接続する場合は、`localhost` を接続先ホスト名に置き換えてください。

```text
http://<LAN_HOST>:8765/mcp
```

Web UIがSSE URLを求める場合は、次を指定してください。

```text
http://<LAN_HOST>:8765/sse
```

HTTPサーバーを起動するPowerShellスクリプトも用意しています。

```powershell
scripts\hermes-http-start.ps1
```

必要なら `.env` に `MCP_HTTP_TOKEN` を設定できます。その場合、Web UI側にも次のHTTPヘッダーを設定してください。

```text
Authorization: Bearer <MCP_HTTP_TOKENの値>
```

## 9. 動作確認

WSL / Ubuntuでテストする場合:

```bash
cd /path/to/SEOAnalytics
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

MCPのstdio応答を確認する場合:

```bash
PYTHONPATH=src python3 -m seo_data_reader_mcp.server < work/mcp_smoke_input.jsonl
```

Dockerでstdio応答を確認する場合:

```powershell
Get-Content work\mcp_smoke_input.jsonl | docker compose run --rm -T seo-data-reader-mcp
```

HTTP版を確認する場合:

```bash
curl -s http://localhost:8765/health
curl -s -X POST http://localhost:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

WSL経由でDockerのstdio応答を確認する場合:

```powershell
wsl -e bash -lc "cd '/path/to/SEOAnalytics' && cat work/mcp_smoke_input.jsonl | docker compose run --rm -T seo-data-reader-mcp"
```

今回の確認では、WSL経由で以下が通っています。

```text
docker compose ps
docker compose build seo-data-reader-mcp
cat work/mcp_smoke_input.jsonl | docker compose run --rm -T seo-data-reader-mcp
```

## 10. 代表的な使い方

WordPress記事一覧を取得:

```text
wp.list_posts
```

特定の記事を取得:

```text
wp.get_post
引数: { "id": 123 }
```

記事本文をプレーンテキスト付きで取得:

```text
seo.get_article_snapshot
引数: { "id": 123 }
```

記事、Search Console、GA4をまとめて取得:

```text
seo.get_article_performance
引数: {
  "id": 123,
  "start_date": "2026-05-01",
  "end_date": "2026-05-31"
}
```

Search Consoleでページごとの検索クエリを取得:

```text
gsc.get_page_queries
引数: {
  "page_url": "https://example.com/article/",
  "start_date": "2026-05-01",
  "end_date": "2026-05-31"
}
```

GA4でページ指標を取得:

```text
ga4.get_page_metrics
引数: {
  "start_date": "2026-05-01",
  "end_date": "2026-05-31"
}
```

## 11. よくあるエラー

`WP_BASE_URL is required`

`.env` に `WP_BASE_URL` が入っていません。

`GSC_ACCESS_TOKEN is required`

Search Console系ツールを使うための認証情報が入っていません。長期運用では `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REFRESH_TOKEN` を入れてください。

`GA4_PROPERTY_ID is required`

GA4の数字だけのプロパティIDが入っていません。

`Google OAuth refresh did not return an access_token`

`GOOGLE_REFRESH_TOKEN` からアクセストークンを取得できませんでした。OAuthスコープ、OAuthクライアントID、クライアントシークレット、refresh tokenを確認してください。

`permission denied while trying to connect to the docker API`

Docker Desktopが起動していないか、現在の環境からDocker APIへ接続できていません。Docker Desktopを起動し、WSL連携や権限を確認してください。

## 12. 安全設計

このMCPサーバーは、最初から事故を避けるために次の方針で作っています。

- MCPツールに書き込み系を用意しない
- WordPressはGETのみ
- Search Consoleは読み取り系エンドポイントのみ
- GA4はData APIのレポート取得のみ
- 認証情報がない場合は明示的にエラーを返す
- リライト、投稿、下書き作成は別MCPに分離する前提

OpenCrawlには、このMCPを「データを読む係」として接続するのがおすすめです。リライト案の作成やWordPressへの反映は、別MCPに分けると安全です。

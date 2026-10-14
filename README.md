# Lab 4 (সংশোধিত): Tool Service + Docker Compose

L3-র সংশোধিত `storage.py`, `memory.py`, `job_queue.py` হুবহু ব্যবহার হয়েছে। বদলেছে `tools.py` (এখন HTTP client), `orchestrator.py` ও `seed_queue.py`।

## ফোল্ডার

```
agent-stack/   storage, memory, job_queue, tools (HTTP client), orchestrator, seed_queue
mcp-server/    server.py, tools_impl.py, Dockerfile  (tool-এর আসল কোড এখানেই)
agent-image/   orchestrator-এর Dockerfile (build context = repo root)
docker-compose.yml, .env.example, .dockerignore
tests/         ১৬টি টেস্ট
```

## প্রথমবার চালানো

```bash
cp .env.example .env
# .env-এ MCP_TOKEN বসান:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose --profile job build
docker compose up -d                     # শুধু tool service চালু হয়
docker compose ps                        # mcp-server কি healthy?
docker compose run --rm agent python seed_queue.py
docker compose run --rm agent            # নাইটলি জব (এক-শটের)
echo $?                                  # 0 সব ঠিক | 1 কিছু ব্যর্থ | 2 tool service অচল
docker compose run --rm agent cat /data/logs/run.log
docker compose down                      # named volume থাকে, state হারায় না
```

নাইটলি cron উদাহরণ:
```
0 2 * * * cd /opt/threadline && docker compose run --rm agent >> /var/log/threadline.log 2>&1
```

## ভার্সন ও টিমের সাথে শেয়ার

```bash
APP_VERSION=0.2.0 docker compose --profile job build
docker tag threadline-mcp:0.2.0 <registry>/threadline-mcp:0.2.0
docker push <registry>/threadline-mcp:0.2.0
```
প্রোডাকশনে `:latest` নয়, নির্দিষ্ট ভার্সন বা digest দিয়ে pull করুন। `GET /health` চলমান ভার্সন দেখায়।

## নতুন tool যোগ করা

শুধু `mcp-server/tools_impl.py`-তে ফাংশন + `server.py`-র `TOOLS`-এ schema সহ এন্ট্রি। client-এ কোনো wrapper লাগে না (`tools.call("নাম", ...)`)। orchestrator-এ ব্যবহার করতে চাইলে `execute()`-এ একটি task type যোগ করুন।

## টেস্ট (Docker ছাড়াই)

```bash
pip install flask requests
python3 -m unittest discover -s tests -v
```

## সীমাবদ্ধতা

- এটা **আসল MCP প্রোটোকল নয়**, MCP-ধাঁচের HTTP সার্ভিস (discovery + schema সহ)। প্রোডাকশনে অফিশিয়াল MCP SDK ব্যবহার করুন।
- Docker, Compose ও gunicorn অংশ আমি এই পরিবেশে চালাতে পারিনি (Docker নেই)। Python কোড ও HTTP আচরণ টেস্ট হয়েছে, কিন্তু image build, healthcheck, `read_only`/`cap_drop` আপনার মেশিনে চালিয়ে যাচাই করুন।
- registry, CI ও image scanning এখানে সেটআপ করা নেই, শুধু কমান্ড দেওয়া আছে।
- at-least-once ও একসাথে একটি orchestrator চালানোর সীমাবদ্ধতা L3-র মতোই।

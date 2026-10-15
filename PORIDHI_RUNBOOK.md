# Poridhi Runbook: Labs 3, 4, 5 (commands + screenshots)

Run everything in the Poridhi VS Code terminal (bash, Docker available). Each step lists the command(s) and the screenshot file to save in `assets/` (the md files already reference these names). Take the screenshot when the output on screen matches "You should see".

Tip: maximise the terminal panel (the square icon) before each screenshot so the full output fits.

---

## 0. Get the fixed code onto Poridhi (once)

On your Windows PC, in `FDE-Lab`:

```bash
git rm agent-stack/test_stack.py          # L3 test; it now lives in lab3-agent-stack/ (it fails against L4 code)
git add -A
git commit -m "fix: L3/L4/L5 review fixes, lab3-agent-stack, capstone, md updates"
git push
```

On Poridhi:

```bash
cd ~/code/FDE-Lab
git pull
pip3 install --break-system-packages requests==2.32.3 flask==3.0.3
```

`.env` is not in git. If `~/code/FDE-Lab/.env` does not exist on Poridhi yet, create it:

```bash
printf 'MCP_TOKEN=%s\nAPP_VERSION=0.2.0\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
```

---

## Lab 3 (folder `lab3-agent-stack/`, plain Python, needs internet for httpbin.org)

```bash
cd ~/code/FDE-Lab/lab3-agent-stack
rm -rf memory logs
```

| # | Command | You should see | Save as |
|---|---|---|---|
| 1 | `python3 -m unittest test_stack -v` | 8 tests, `OK` | `assets/l3_unit_resiliency_tests.png` |
| 2 | `python3 seed_queue.py && python3 orchestrator.py; echo "exit=$?"` | 3 `Completed vendor-...`, `finished: {'done': 3}`, `exit=0` | `assets/l3_orchestrator_run.png` |
| 3 | `python3 simulate_crash.py` | run 1 `exit code: 137`, `"vendor-a": "done"`, run 2 `Skipping vendor-a`, `exit code: 0` | `assets/l3_crash_recovery_proof.png` |
| 4 | `cat memory/state.json; ls memory/history` | `run_id: crash-demo-...`, all `done`; today's run archived in history | `assets/l3_state_json_proof.png` |

If a vendor shows `failed` with HTTP 502/503, httpbin.org is having a bad moment. Rerun the same command (failed tasks are retried) or wait a minute.

---

## Lab 4 (repo root, Docker)

```bash
cd ~/code/FDE-Lab
export COMPOSE_FILE=docker-compose.yml:docker-compose.debug.yml
docker compose down -v     # optional: start from an empty agent_data volume so nothing is "already done"
```

| # | Command | You should see | Save as |
|---|---|---|---|
| 1 | `python3 -m unittest discover -s tests -v` | 16 tests, `OK` | `assets/l4_unit_tests.png` |
| 2 | `docker compose --profile job build` then `docker compose up -d --wait && docker compose ps && docker compose logs mcp-server \| tail -5` | `mcp-server ... (healthy)`, `127.0.0.1:8080->8080`, gunicorn `Listening at: http://0.0.0.0:8080` | `assets/l4_mcp_server_startup.png` |
| 3 | `TOKEN=$(sed -n 's/^MCP_TOKEN=//p' .env)` (not grep: Poridhi aliases grep to `--color=always`, which breaks the token)<br>`curl -s http://127.0.0.1:8080/health; echo`<br>`curl -s -o /dev/null -w "without token -> HTTP %{http_code}\n" http://127.0.0.1:8080/tools`<br>`curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/tools \| python3 -m json.tool \| head -40` | health JSON, `HTTP 401`, then the tool schemas | `assets/l4_mcp_tools_discovery.png` |
| 4 | `docker compose run --rm agent python seed_queue.py`<br>`docker compose run --rm agent; echo "exit=$?"` | `Seeded 4 tasks`, 4 `Completed`, `finished: {'done': 4}`, `exit=0` | `assets/l4_orchestrator_batch_run.png` |
| 5 | `docker compose run --rm -e RUN_ID=crash-test -e CRASH_AFTER=1 agent; echo "exit=$?"`<br>`docker compose run --rm -e RUN_ID=crash-test agent; echo "exit=$?"` | `CRASH HOOK`, `exit=137`, then `Skipping vendor-a (already done in run crash-test)`, `exit=0` | `assets/l4_crash_recovery_docker.png` |

Optional extra proof (same screenshot or a new one): `docker compose run --rm agent id` shows `uid=10001(agent)`, not root.

**Leave the tool service running** for the Lab 5 capstone.

---

## Lab 5 (folder `lab5-inference/`, Docker, CPU)

**Low-RAM machines (Poridhi default: 1.9 GB RAM, 2 CPUs).** DeepSeek-R1 1.5b needs about 1.7 GB, so the client gets `HTTP 500` and the server log says `model requires more system memory (1.7 GiB) than is available`. Add swap once per machine (Ollama counts swap as memory):

```bash
sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
free -h
```

Generation is then about 4 tokens/s, so one DeepSeek answer takes 2 to 4 minutes, longer than the client's default 120 s timeout (you'd see `500 | 2m0s` in the server log). Run the client and capstone with longer timeouts:

```bash
INFERENCE_TIMEOUT=600 docker compose run --rm inference-client
INFERENCE_TIMEOUT=900 CAPSTONE_MAX_TOKENS=1024 python3 capstone/agent_loop.py
```

Open a **second terminal**:

```bash
cd ~/code/FDE-Lab/lab5-inference
export COMPOSE_FILE=docker-compose.yml:docker-compose.debug.yml
docker compose up -d --build --wait
```

Your existing screenshots `l5_poridhi_server_startup.png`, `l5_server_startup_healthy.png`, `l5_client_summarize_proof.png` and `l5_gpu_check_proof.png` are still valid. Retake only:

| # | Command | You should see | Save as |
|---|---|---|---|
| 1 | `python3 -m unittest discover -s tests -v` | **36** tests, `OK` (was 20) | `assets/l5_unit_tests_proof.png` |
| 2 | `python3 capstone/agent_loop.py; echo "exit=$?"` | `Discovered tools: [3 names]`, `DeepSeek decided ... fetch_vendor_status({'vendor_id': 'A'})`, `Tool result: {'ok': True ...}`, summary, `[L3] State saved`, `exit=0` | `assets/l5_capstone_run.png` |

The capstone is slow on CPU (each DeepSeek call is 20 s to 2 min). Seeing `attempt 1/3 rejected: ...` before a success is normal for the 1.5b model, and worth having in the screenshot, because it shows the validation working. Run it a second time to show the L3 skip (`already done in run ...`); `RUN_ID=demo-2 python3 capstone/agent_loop.py` forces a fresh run.

---

## Clean up

```bash
cd ~/code/FDE-Lab/lab5-inference && docker compose down      # model stays in the ollama_models volume
cd ~/code/FDE-Lab && docker compose down                     # state stays in the agent_data volume
```

Then copy the new screenshots into `assets/` on your PC, commit and push.

# Lab 5 (সংশোধিত): লোকাল LLM (Ollama + DeepSeek) সার্ভিস ও client

দুটি কন্টেইনার: `inference-server` (Ollama) ও একটি one-shot `inference-client`। client শুধু HTTP-তে OpenAI-compatible `/v1/chat/completions` ডাকে।

## ডিফল্ট হলো CPU, GPU আলাদা ফাইলে

```bash
cp .env.example .env            # ঐচ্ছিক, সব মানের ডিফল্ট আছে

# CPU (যেকোনো মেশিনে চলে, ধীর)
docker compose up -d --build --wait
# NVIDIA GPU (driver + NVIDIA Container Toolkit লাগবে)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build --wait

docker compose run --rm inference-client      # সারাংশ প্রিন্ট করে, exit code দেয়
sh scripts/check_gpu.sh                       # সত্যিই GPU-তে চলছে কি না
docker compose down                           # মডেল volume-এ থাকে, আবার নামাতে হয় না
```

প্রথমবার মডেল নামাতে সময় লাগে (`--wait` ততক্ষণ অপেক্ষা করে)। `docker compose down -v` দিলে volume-সহ মডেল মুছে যায়।

## কী কী ঠিক করা হয়েছে

| মূল ল্যাবের সমস্যা | সমাধান |
|---|---|
| `$$!` ও `$$OLLAMA_PID` ভুল, `\` দিয়ে JSON লাইন ভাঙা | আলাদা `server/entrypoint.sh` |
| "build-এ মডেল pull" ভুল, `down`-এ মডেল হারায় | named volume `ollama_models:/root/.ollama` |
| healthcheck সার্ভার চালু হলেই পাস | মডেল তালিকায় আসার পর পাস (`ollama list`, ট্যাগ হুবহু মিলিয়ে) |
| pull ব্যর্থ হলেও কন্টেইনার "চালু" | ব্যর্থ হলে exit 1, রিস্টার্ট সীমিত (`on-failure:3`) |
| `docker stop`-এ অপরিষ্কার বন্ধ | SIGTERM/EXIT trap, child প্রসেস বন্ধ হয় |
| `USER ollama` ও `wget` নির্ভরতা | বাদ। healthcheck-এ `ollama list`, `wget` লাগে না |
| 7b-তে গেলে 404 | client আগে `/v1/models` দেখে, না থাকলে ঠিক pull কমান্ড বলে (exit 3) |
| CPU fallback-এর ভুল `--build-arg` | `docker-compose.gpu.yml` override; ডিফল্ট CPU |
| GPU ব্যবহার হচ্ছে কি না যাচাই নেই | `scripts/check_gpu.sh` (`ollama ps`-এর PROCESSOR) |
| "মডেল মেমোরিতে থেকে যায়" | `OLLAMA_KEEP_ALIVE=30m` দিয়ে বাস্তবে নিশ্চিত |
| DeepSeek-R1-এর `<think>` আউটপুটে | client ডিফল্টে সরিয়ে দেয় (`STRIP_THINK=0` দিলে থাকে) |
| client-এ retry/timeout/exit code নেই | retry (connect/5xx), `max_tokens`, `temperature=0`, exit code 0/2/3/4 |
| `tail -f /dev/null` + `exec` | one-shot `docker compose run --rm inference-client` |
| ইন্টারনেট ছাড়া চলে না | `PULL_ON_START=0` offline মোড (নিচে দেখুন) |

## মডেল বদলানো

`.env`-এ `LLM_MODEL=deepseek-r1:7b` দিয়ে `docker compose up -d --wait` চালান। সার্ভার রিস্টার্ট হয়ে নতুন মডেল নামায় (পুরোনোটা volume-এ থাকে)। client কোড, image বা compose বদলাতে হয় না।

## Air-gapped / ইন্টারনেট-বিহীন ব্যবহার

১. ইন্টারনেট আছে এমন মেশিনে একবার মডেল নামান এবং volume ব্যাকআপ নিন:
```bash
docker run --rm -v lab5-fixed_ollama_models:/data -v "$PWD":/backup alpine \
  tar czf /backup/models.tgz -C /data .
```
(volume-এর আসল নাম `docker volume ls`-এ দেখুন, প্রজেক্ট ফোল্ডারের নাম প্রিফিক্স থাকে।)
২. ফাইলটি অফলাইন মেশিনে নিয়ে volume বানিয়ে খুলুন:
```bash
docker volume create lab5-fixed_ollama_models
docker run --rm -v lab5-fixed_ollama_models:/data -v "$PWD":/backup alpine \
  tar xzf /backup/models.tgz -C /data
```
৩. `.env`-এ `PULL_ON_START=0` দিন। মডেল volume-এ না থাকলে কন্টেইনার স্পষ্ট এরর দিয়ে থেমে যায়।

## হোস্ট থেকে curl করতে চাইলে

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
curl -s http://localhost:11434/v1/models
```

## টেস্ট (Docker, GPU বা ইন্টারনেট ছাড়াই)

```bash
pip install requests
python3 -m unittest discover -s tests -v
```
`test_client.py` একটি স্টাব OpenAI-সার্ভারের সাথে client যাচাই করে। `test_entrypoint.py` নকল `ollama` ব্যবহার করে entrypoint.sh-এর সব পথ (আছে/নেই/pull ব্যর্থ/offline/SIGTERM) যাচাই করে।

## সীমাবদ্ধতা

- **Docker, আসল Ollama, DeepSeek ও GPU অংশ আমি চালাতে পারিনি।** আপনার মেশিনে `docker compose up` থেকে `ollama ps` পর্যন্ত যাচাই করুন। নির্দিষ্ট করে দেখুন: `ollama list`-এর আউটপুটের প্রথম কলামে `deepseek-r1:1.5b` ঠিক এভাবেই আসে কি না (healthcheck এর ওপর নির্ভর করে)।
- `OLLAMA_VERSION=0.5.7` ল্যাবের মূল পিন। পুরোনো হতে পারে; DeepSeek-R1 না চললে নতুন ট্যাগ দিন (`.env`-এ)।
- সার্ভার কন্টেইনার image-এর ডিফল্ট ইউজারে (root) চলে। অফিশিয়াল image এভাবেই বানানো, আর আমি non-root চালিয়ে যাচাই করিনি। client non-root ও সীমিত।
- Docker Desktop (Mac) এ GPU passthrough নেই। Windows-এ WSL2 + NVIDIA ড্রাইভার লাগে।
- এটি এখনো orchestrator (L3/L4)-এর সাথে জোড়া লাগানো নয়, আলাদা ল্যাব হিসেবে আছে।

# Lab 3 (সংশোধিত): Agent Stack

## কী কী বদলেছে (মূল L3-এর তুলনায়)

| সমস্যা | সমাধান |
|---|---|
| দ্বিতীয় রাতে সব কাজ skip হতো | state এখন `run_id` (ডিফল্ট: আজকের তারিখ) অনুযায়ী। নতুন run_id মানে নতুন শুরু, আগেরটা `memory/history/`-তে archive হয় |
| ব্যর্থ কাজও "done" হতো | প্রতি টাস্কের `status`: `running / done / failed`। শুধু `ok: true` হলেই `done`। ব্যর্থ হলে সর্বোচ্চ ৩ বার retry (backoff সহ), পরের রানে আবার চেষ্টা |
| ফাইল লেখার মাঝে ক্র্যাশে state নষ্ট | atomic write: temp ফাইল → fsync → `os.replace` |
| `queue.py` stdlib `queue`-কে ঢেকে দিত | নাম `job_queue.py` |
| ক্র্যাশ সিমুলেশন ছিল হাতে cursor বদলানো | আসল সিমুলেশন: প্রসেস `os._exit(137)` দিয়ে মরে, তারপর আবার চলে |
| `float(weight)` ক্র্যাশ করত | অসংখ্যাসূচক ওজনকে anomaly ধরা হয় |
| লগে সময় ছিল না | UTC timestamp সহ লগ |
| কোনো টেস্ট ছিল না | `test_stack.py` (৮টি টেস্ট, আসল hard-kill টেস্টসহ) |

cursor বাদ দেওয়া হয়েছে, কারণ প্রতি টাস্কের status-ই আসল সত্য। এতে "cursor আর processed তালিকা আলাদা হয়ে যাওয়া" ধরনের গোলমাল হয় না।

## চালানোর নিয়ম

```bash
pip install -r requirements.txt
python3 -m unittest test_stack -v     # আগে টেস্ট (ইন্টারনেট লাগে না)

python3 seed_queue.py                 # কিউ সিড
python3 orchestrator.py               # চালান
python3 simulate_crash.py             # আসল ক্র্যাশ + রিকভারি ডেমো (ইন্টারনেট লাগে)
cat logs/run.log
cat memory/state.json
```

কমান্ড `lab3-agent-stack` ফোল্ডারের ভেতর থেকে চালাতে হবে। (`agent-stack/` ফোল্ডারে এখন L4-এর কোড, তাই L3-এর টেস্ট সেখানে চলবে না।)

## Environment variable

| নাম | ডিফল্ট | কাজ |
|---|---|---|
| `RUN_ID` | আজকের UTC তারিখ | কোন রানের state ব্যবহার হবে |
| `MAX_ATTEMPTS` | 3 | প্রতি টাস্কে retry সীমা |
| `RETRY_BACKOFF` | 0.5 | retry-র মধ্যে অপেক্ষা (সেকেন্ড, দ্বিগুণ হয়) |
| `VENDOR_API_URL` | httpbin.org/get | ভেন্ডর API-র ঠিকানা |
| `AGENT_DATA_DIR` / `AGENT_LOG_DIR` | memory / logs | state ও লগের ফোল্ডার |
| `CRASH_AFTER` | বন্ধ | শুধু টেস্টের জন্য: N টাস্কের পর হঠাৎ মরে যাওয়া |

## সীমাবদ্ধতা (জেনে রাখুন)

- **at-least-once:** টুল চলার পর কিন্তু `done` সেভ হওয়ার আগে ক্র্যাশ করলে সেই টাস্ক আবার চলবে। তাই টুল idempotent হওয়া উচিত (স্ট্যাটাস আনা idempotent)।
- একসাথে দুটি orchestrator একই state ফাইলে চালালে সমস্যা হবে। একটিই চালান (দরকার হলে lock যোগ করতে হবে)।
- এখনো কোনো LLM নেই। orchestrator-এর সিদ্ধান্ত `if type == "status"`। LLM যোগ করার জায়গা `execute()` ফাংশন।

import job_queue

tasks = [
    {"id": "vendor-a", "type": "status", "vendor_id": "A"},
    {"id": "vendor-b", "type": "status", "vendor_id": "B"},
    {"id": "vendor-c", "type": "status", "vendor_id": "C"},
]
job_queue.seed(tasks)
print(f"Seeded {len(tasks)} tasks into the queue")

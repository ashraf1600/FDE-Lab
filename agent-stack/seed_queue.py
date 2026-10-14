import job_queue

SAMPLE_MANIFEST = "shipment_id,weight\nS1,12.5\nS2,0\nS3,\nS4,abc\n"

tasks = [
    {"id": "vendor-a", "type": "status", "vendor_id": "A"},
    {"id": "vendor-b", "type": "status", "vendor_id": "B"},
    {"id": "vendor-c", "type": "status", "vendor_id": "C"},
    {"id": "manifest-1", "type": "manifest", "csv": SAMPLE_MANIFEST},
]
job_queue.seed(tasks)
print(f"Seeded {len(tasks)} tasks into the queue")

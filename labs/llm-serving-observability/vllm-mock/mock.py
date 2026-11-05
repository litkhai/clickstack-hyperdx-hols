"""Stand-in for vLLM, standard library only: an OpenAI-compatible /v1/chat/completions and a
Prometheus /metrics page with vLLM's metric names, so the Collector's keep filter and the
dashboard tiles have something real to read.

It is not a model. Each request answers a fixed text and moves the counters the way a
prefix-cached agent loop would: the prompt grows with the messages sent, and most prompt
tokens are cache hits. Two families are exposed on purpose although the Collector's filter
does not keep them (vllm:estimated_read_bytes_per_gpu, vllm:cache_config_info): check.py
asserts that they never reach ClickHouse.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "mock"
LABELS = 'engine="0",model_name="mock"'
BUCKETS = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
lock = threading.Lock()
state = {
    "prompt_tokens_total": 0, "generation_tokens_total": 0, "prompt_tokens_cached_total": 0,
    "prefix_cache_queries_total": 0, "prefix_cache_hits_total": 0,
    "request_success_total": 0, "num_preemptions_total": 0,
}
hist = {name: [0] * len(BUCKETS) + [0, 0.0] for name in
        ("time_to_first_token_seconds", "e2e_request_latency_seconds", "inter_token_latency_seconds")}


def observe(name, value):
    h = hist[name]
    for i, le in enumerate(BUCKETS):
        if value <= le:
            h[i] += 1
    h[-2] += 1
    h[-1] += value


def metrics_text():
    out = []
    with lock:
        for k, v in state.items():
            out += [f"# TYPE vllm:{k} counter", f"vllm:{k}{{{LABELS}}} {v}"]
        out += ["# TYPE vllm:num_requests_running gauge", f"vllm:num_requests_running{{{LABELS}}} 0",
                "# TYPE vllm:num_requests_waiting gauge", f"vllm:num_requests_waiting{{{LABELS}}} 0",
                "# TYPE vllm:kv_cache_usage_perc gauge",
                f"vllm:kv_cache_usage_perc{{{LABELS}}} {min(0.9, state['prompt_tokens_total'] / 2e6):.4f}"]
        for name, h in hist.items():
            out.append(f"# TYPE vllm:{name} histogram")
            for i, le in enumerate(BUCKETS):
                out.append(f'vllm:{name}_bucket{{{LABELS},le="{le}"}} {h[i]}')
            out += [f'vllm:{name}_bucket{{{LABELS},le="+Inf"}} {h[-2]}',
                    f"vllm:{name}_count{{{LABELS}}} {h[-2]}", f"vllm:{name}_sum{{{LABELS}}} {h[-1]:.6f}"]
        # Exposed but not kept by the Collector's metric_relabel_configs (S3 negative).
        out += ["# TYPE vllm:estimated_read_bytes_per_gpu gauge", f"vllm:estimated_read_bytes_per_gpu{{{LABELS}}} 0.0",
                "# TYPE vllm:cache_config_info gauge", f'vllm:cache_config_info{{{LABELS},block_size="16"}} 1']
    return "\n".join(out) + "\n"


def complete(body):
    messages = body.get("messages") or []
    prompt = sum(len(str(m.get("content", ""))) for m in messages) // 4 + 8 * len(messages)
    completion = 16
    cached = int(prompt * 0.9)
    with lock:
        state["prompt_tokens_total"] += prompt
        state["generation_tokens_total"] += completion
        state["prompt_tokens_cached_total"] += cached
        state["prefix_cache_queries_total"] += prompt
        state["prefix_cache_hits_total"] += cached
        state["request_success_total"] += 1
        observe("time_to_first_token_seconds", 0.04 + prompt / 40000)
        observe("e2e_request_latency_seconds", 0.04 + prompt / 40000 + completion * 0.03)
        observe("inter_token_latency_seconds", 0.03)
    return {
        "id": f"chatcmpl-mock-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok — this is the mock vLLM."}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/metrics":
            self._send(200, metrics_text().encode(), "text/plain; version=0.0.4")
        elif self.path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/chat/completions":
            self._send(200, complete(body))
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("mock vLLM on :8000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()

#!/usr/bin/env python3
"""
WAF Telemetry Generator
Generates OpenTelemetry traces, logs, and metrics for WAF simulation
"""

import os
import time
import threading
from flask import Flask, jsonify, request
from waf_generator import WAFTelemetryGenerator

app = Flask(__name__)

# Global state
generator = None
generation_thread = None
is_running = False
stats = {
    "total_events": 0,
    "normal_requests": 0,
    "attack_requests": 0,
    "blocked_requests": 0,
    "allowed_requests": 0,
    "aws_requests": 0,
    "azure_requests": 0,
    "gcp_requests": 0,
    "start_time": None
}

def reset_stats():
    """Reset statistics"""
    global stats
    stats = {
        "total_events": 0,
        "normal_requests": 0,
        "attack_requests": 0,
        "blocked_requests": 0,
        "allowed_requests": 0,
        "aws_requests": 0,
        "azure_requests": 0,
        "gcp_requests": 0,
        "start_time": time.time()
    }

def generation_loop():
    """Main generation loop"""
    global is_running, stats, generator

    print("🚀 Starting WAF telemetry generation...")
    reset_stats()

    while is_running:
        try:
            # Generate one event
            event_stats = generator.generate_event()

            # Update statistics
            stats["total_events"] += 1

            if event_stats["is_attack"]:
                stats["attack_requests"] += 1
            else:
                stats["normal_requests"] += 1

            if event_stats["action"] == "block":
                stats["blocked_requests"] += 1
            else:
                stats["allowed_requests"] += 1

            cloud_provider = event_stats["cloud_provider"].lower()
            stats[f"{cloud_provider}_requests"] += 1

            # Sleep to maintain target rate
            time.sleep(1.0 / generator.events_per_second)

        except Exception as e:
            print(f"❌ Error generating event: {e}")
            time.sleep(1)

    print("🛑 WAF telemetry generation stopped")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

@app.route('/start', methods=['POST'])
def start_generation():
    """Start generating telemetry"""
    global is_running, generation_thread, generator

    if is_running:
        return jsonify({"error": "Generation already running"}), 400

    # Initialize generator if not exists
    if generator is None:
        generator = WAFTelemetryGenerator()

    is_running = True
    generation_thread = threading.Thread(target=generation_loop, daemon=True)
    generation_thread.start()

    return jsonify({"status": "started"}), 200

@app.route('/stop', methods=['POST'])
def stop_generation():
    """Stop generating telemetry"""
    global is_running

    if not is_running:
        return jsonify({"error": "Generation not running"}), 400

    is_running = False

    # Wait for thread to finish
    if generation_thread and generation_thread.is_alive():
        generation_thread.join(timeout=5)

    return jsonify({"status": "stopped"}), 200

@app.route('/status', methods=['GET'])
def get_status():
    """Get current generation status and statistics"""
    global is_running, stats

    # Calculate runtime
    runtime = 0
    events_per_second = 0
    if stats["start_time"]:
        runtime = time.time() - stats["start_time"]
        if runtime > 0:
            events_per_second = stats["total_events"] / runtime

    return jsonify({
        "is_running": is_running,
        "stats": stats,
        "runtime_seconds": round(runtime, 2),
        "current_rate": round(events_per_second, 2)
    }), 200

@app.route('/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    return jsonify({
        "events_per_second": int(os.getenv('EVENTS_PER_SECOND', 100)),
        "normal_traffic_ratio": float(os.getenv('NORMAL_TRAFFIC_RATIO', 0.80)),
        "cloud_ratios": {
            "aws": int(os.getenv('AWS_RATIO', 6)),
            "azure": int(os.getenv('AZURE_RATIO', 1)),
            "gcp": int(os.getenv('GCP_RATIO', 3))
        }
    }), 200

if __name__ == '__main__':
    port = int(os.getenv('GENERATOR_PORT', 7651))
    print(f"🌐 Starting WAF Generator API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

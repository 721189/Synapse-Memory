import logging
import json
import time
from typing import Dict, Any, Optional
from functools import wraps

# Structured Logger Setup
logger = logging.getLogger("SynapseObservability")

def log_event(event_name: str, status: str, details: Dict[str, Any], level: int = logging.INFO):
    """Logs structured JSON events for production analysis."""
    log_record = {
        "event": event_name,
        "status": status,
        "timestamp": time.time(),
        "details": details
    }
    logger.log(level, json.dumps(log_record))

def instrument_operation(operation_name: str):
    """Decorator to instrument method execution times and failures."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                log_event(operation_name, "SUCCESS", {"duration_ms": (time.perf_counter() - start) * 1000})
                return result
            except Exception as e:
                log_event(operation_name, "FAILURE", {"error": str(e), "duration_ms": (time.perf_counter() - start) * 1000}, level=logging.ERROR)
                raise e
        return wrapper
    return decorator

class MetricsCollector:
    """Simple in-memory metrics collector."""
    def __init__(self):
        self.metrics: Dict[str, float] = {}

    def increment(self, metric_name: str, value: float = 1.0):
        self.metrics[metric_name] = self.metrics.get(metric_name, 0.0) + value

    def get_metrics(self) -> Dict[str, float]:
        return self.metrics

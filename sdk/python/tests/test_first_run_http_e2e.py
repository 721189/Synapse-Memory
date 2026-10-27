"""Real HTTP smoke test for the documented local-first quickstart.

Runs a fresh Uvicorn process with isolated SQLite storage and no configured API
key, then exercises readiness, memory creation, and retrieval through the public
Python SDK. No external database or embedding provider is required.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from cryptography.fernet import Fernet

from synapse_memory import SynapseClient


class TestFirstRunHttpE2E(unittest.TestCase):
    def test_local_sqlite_write_retrieve_and_readiness(self):
        with tempfile.TemporaryDirectory(prefix="synapse-first-run-") as temp_dir:
            temp_path = Path(temp_dir)

            # Reserve an ephemeral port, then release it before starting Uvicorn.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            env = os.environ.copy()
            # Do not inherit developer/CI database or auth settings: this test
            # specifically verifies the documented unconfigured local-dev path.
            for name in (
                "DATABASE_URL",
                "POSTGRES_URL",
                "SYNAPSE_API_KEY",
                "SYNAPSE_ENCRYPTION_KEYS",
                "SYNAPSE_ENCRYPTION_KEY_B64_FILE",
                "NODE_ENV",
                "REDIS_URL",
            ):
                env.pop(name, None)

            env["SYNAPSE_STORAGE_BACKEND"] = "sqlite"
            env["SYNAPSE_SQLITE_DB_PATH"] = str(temp_path / "synapse_memory.db")
            env["SYNAPSE_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
            env["SYNAPSE_KEY_FILE"] = str(temp_path / ".synapse_key")
            sdk_root = str(Path(__file__).resolve().parents[1])
            inherited_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (sdk_root, inherited_pythonpath) if part
            )

            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "synapse_memory.api.fastapi_server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=temp_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            base_url = "http://127.0.0.1:{0}".format(port)

            try:
                deadline = time.monotonic() + 20.0
                last_error = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output = process.communicate()[0]
                        self.fail(
                            "Uvicorn exited before readiness (code {0}):\n{1}".format(
                                process.returncode, output
                            )
                        )
                    try:
                        with urlopen(base_url + "/readiness", timeout=1.0) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError, OSError) as exc:
                        last_error = exc
                        time.sleep(0.2)
                else:
                    process.terminate()
                    output = process.communicate(timeout=5)[0]
                    self.fail(
                        "Engine did not become ready within 20 seconds. "
                        "Last connection error: {0}\n{1}".format(last_error, output)
                    )

                sentence = (
                    "SynapseQuickstartZebra prefers SQLite for local demos "
                    "and PostgreSQL for production."
                )
                with SynapseClient(
                    base_url,
                    tenant_id="local-dev",
                    timeout=3.0,
                    max_retries=0,
                ) as client:
                    readiness = client.readiness()
                    self.assertEqual(readiness["status"], "READY")
                    self.assertEqual(readiness["storage_backend"], "sqlite")

                    created = client.create_memory(sentence, category="fact")
                    self.assertTrue(created.id)
                    self.assertEqual(created.tenant_id, "local-dev")
                    self.assertTrue(created.status.startswith("SUCCESS_"))

                    result = client.retrieve(
                        "What database does SynapseQuickstartZebra prefer for local demos?",
                        max_tokens=300,
                    )
                    self.assertGreaterEqual(result.injected_nodes_count, 1)
                    self.assertIn("SynapseQuickstartZebra", result.fused_context)
                    self.assertIn("SQLite", result.fused_context)
                    self.assertEqual(result.storage_backend, "sqlite")
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()

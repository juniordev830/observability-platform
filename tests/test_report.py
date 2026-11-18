import os
from pathlib import Path
import shutil
import subprocess
import unittest

from server import Metrics


@unittest.skipUnless(shutil.which("awk"), "requer awk")
class ReportTest(unittest.TestCase):
    def report(self, metrics):
        script = Path(__file__).resolve().parents[1] / "tools" / "resumo-http.awk"
        return subprocess.run(
            ["awk", "-f", str(script)], input=metrics, text=True,
            capture_output=True, env={**os.environ, "LC_ALL": "C"},
        )

    def test_contagem_e_percentual(self):
        metrics = Metrics()
        for path in ("/health", "/ready", "/inexistente"):
            metrics.observe(path)
        result = self.report(metrics.render())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2xx\t2\t66.67\n", result.stdout)
        self.assertIn("4xx\t1\t33.33\n", result.stdout)

    def test_sem_requisicoes(self):
        result = self.report(Metrics().render())
        self.assertEqual(result.returncode, 0)
        self.assertIn("2xx\t0\t0.00\n", result.stdout)

    def test_coleta_invalida(self):
        for data in ("", Metrics().render() * 2, 'demo_responses_total{status="200"} x'):
            with self.subTest(data=data):
                result = self.report(data)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

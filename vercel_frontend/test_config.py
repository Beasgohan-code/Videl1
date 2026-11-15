"""Local regression checks for the Vercel front-door configuration."""
import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


class FrontDoorConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

    def test_only_studio_and_api_paths_are_proxied(self):
        rewrites = {item["source"]: item["destination"] for item in self.config["rewrites"]}
        self.assertEqual(rewrites, {
            "/app": "https://videl1.onrender.com/app",
            "/app/:path*": "https://videl1.onrender.com/app/:path*",
            "/api/v1/:path*": "https://videl1.onrender.com/api/v1/:path*",
        })
        for destination in rewrites.values():
            url = urlsplit(destination)
            self.assertEqual(url.scheme, "https")
            self.assertIsNone(url.username)
            self.assertIsNone(url.password)
            self.assertEqual(url.query, "")
            self.assertEqual(url.fragment, "")

    def test_no_store_security_headers(self):
        rules = {rule["source"]: rule["headers"] for rule in self.config["headers"]}
        headers = {item["key"].lower(): item["value"] for item in rules["/(.*)"]}
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["referrer-policy"], "no-referrer")

    def test_root_and_admin_redirects(self):
        redirects = {item["source"]: item for item in self.config["redirects"]}
        self.assertEqual(redirects["/"]["destination"], "/app")
        self.assertEqual(redirects["/admin"]["destination"], "https://videl1.onrender.com/admin")
        self.assertFalse(redirects["/"]["permanent"])
        self.assertFalse(redirects["/admin"]["permanent"])


if __name__ == "__main__":
    unittest.main()

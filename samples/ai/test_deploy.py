import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("deploy.sh")
APP_ID = "11111111-1111-4111-8111-111111111111"
TENANT_ID = "22222222-2222-4222-8222-222222222222"
BASE_ARGS = [
    "--resource-group", "test-rg",
    "--relay-namespace-name", "test-relay",
    "--apim-name", "test-apim",
    "--apim-publisher-email", "test@example.com",
    "--apim-publisher-name", "Test",
    "--cluster-name", "test-cluster",
    "--aio-extension-name", "test-extension",
]
MOCK_TOOL = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["DEPLOY_TEST_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")

if tool == "az":
    if args[:2] == ["account", "show"]:
        if "--query" in args:
            query = args[args.index("--query") + 1]
            print("test@example.com" if query == "user.name" else
                  "22222222-2222-4222-8222-222222222222")
        sys.exit(0)
    if args[:3] == ["ad", "app", "list"]:
        print("[]")
        sys.exit(0)
    if args[:3] in (["ad", "app", "create"], ["deployment", "group", "create"]):
        sys.exit(75)
elif tool == "helm" and args[0] == "pull":
    destination = Path(args[args.index("--destination") + 1])
    version = args[args.index("--version") + 1]
    (destination / f"aio-ai-relay-{version}.tgz").touch()
    sys.exit(0)
elif tool == "kubectl" and args == ["config", "current-context"]:
    print("test-context")
    sys.exit(0)

sys.exit(f"Unexpected mocked command: {tool} {args!r}")
"""


class ServiceManagementReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.log = root / "commands.jsonl"
        mock_bin = root / "bin"
        mock_bin.mkdir()
        for tool in ("az", "helm", "kubectl"):
            executable = mock_bin / tool
            executable.write_text(MOCK_TOOL)
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{mock_bin}{os.pathsep}{os.environ['PATH']}",
            "DEPLOY_TEST_LOG": str(self.log),
        }

    def run_deploy(self, *args):
        self.log.write_text("")
        result = subprocess.run(
            ["bash", str(SCRIPT), *BASE_ARGS, *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        commands = [
            json.loads(line) for line in self.log.read_text().splitlines()
        ]
        return result, commands

    def test_reference_is_forwarded_as_one_argument(self):
        reference = "approved service reference"
        result, commands = self.run_deploy(
            "--service-management-reference", reference,
        )
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
        create = next(
            command for command in commands
            if command[:4] == ["az", "ad", "app", "create"]
        )
        self.assertEqual(
            create,
            ["az", "ad", "app", "create", "--display-name", "aio-ai-relay",
             "--service-management-reference", reference, "-o", "json"],
        )

    def test_omitted_reference_preserves_app_creation(self):
        result, commands = self.run_deploy()
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
        create = next(
            command for command in commands
            if command[:4] == ["az", "ad", "app", "create"]
        )
        self.assertEqual(
            create,
            ["az", "ad", "app", "create", "--display-name", "aio-ai-relay",
             "-o", "json"],
        )

    def test_reference_requires_a_nonempty_value(self):
        for suffix in ([], [""], ["   "], ["--endpoint-protocol", "mcp"]):
            with self.subTest(suffix=suffix):
                result, commands = self.run_deploy(
                    "--service-management-reference", *suffix,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    "--service-management-reference requires a non-empty value",
                    result.stderr,
                )
                self.assertEqual(commands, [])

    def test_reference_is_rejected_for_an_existing_app(self):
        result, commands = self.run_deploy(
            "--entra-app-id", APP_ID,
            "--entra-tenant-id", TENANT_ID,
            "--service-management-reference", "approved-reference",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("existing-app IDs cannot be combined", result.stderr)
        self.assertIn("--service-management-reference", result.stderr)
        self.assertEqual(commands, [])

    def test_existing_app_without_reference_skips_graph(self):
        result, commands = self.run_deploy(
            "--entra-app-id", APP_ID,
            "--entra-tenant-id", TENANT_ID,
        )
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
        self.assertFalse(any(command[:2] == ["az", "ad"] for command in commands))
        self.assertTrue(any(
            command[:4] == ["az", "deployment", "group", "create"]
            for command in commands
        ))


if __name__ == "__main__":
    unittest.main()

import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import paramiko
from typer.testing import CliRunner
from rich.console import Console

from abzctl import config, main, ui
from abzctl.core.analyzer import EnvVar, ProjectAnalyzer, ProjectPlan
from abzctl.core.caddy import CaddyManager, CADDYFILE, find_unmanaged_conflict, upsert_block
from abzctl.core.codescan import infer_requirements, is_worker
from abzctl.core.deployer import Deployer, project_name_from_repo, validate_repo_url
from abzctl.core.services import new_credentials, parse_services
from abzctl.core.ssh_client import CommandResult, ServerCredentials, SSHClient
from abzctl.exceptions import CaddyError, DeployError, RemoteCommandError, SSHConnectionError


class ValidationTests(unittest.TestCase):
    def test_project_path_traversal_and_compose_names(self):
        for name in ("../../etc", "/etc", ".", "a/b", "UPPER", "-app", "a;id", "a" * 64):
            with self.subTest(name=name), self.assertRaises(DeployError):
                Deployer(MagicMock(), "https://github.com/u/app.git", project_name=name)

    def test_derived_name_is_docker_compatible(self):
        self.assertEqual(project_name_from_repo("git@github.com:u/My.App.git"), "my-app")

    def test_credentials_are_rejected_and_redacted(self):
        with self.assertRaises(DeployError) as error:
            validate_repo_url("https://PRIVATE_TOKEN@github.com/u/app.git")
        self.assertNotIn("PRIVATE_TOKEN", error.exception.message)
        error = RemoteCommandError("git clone https://PRIVATE_TOKEN@github.com/u/app.git", 1)
        self.assertNotIn("PRIVATE_TOKEN", error.message)

    def test_code_imports_ignore_strings_and_relative_imports(self):
        sources = {"main.py": '"""import fake_package"""\nimport os, aiogram as bot\nfrom .handlers import x\nfrom typing_extensions import Self\n'}
        self.assertEqual(infer_requirements(sources), ["aiogram", "typing_extensions"])

    def test_worker_and_web_detection(self):
        self.assertEqual(is_worker({"main.py": "import aiogram"}), "aiogram")
        self.assertIsNone(is_worker({"main.py": "import aiogram\nimport fastapi"}))

    def test_services_reuse_and_corruption(self):
        credentials = new_credentials("postgres", "app")
        self.assertEqual(parse_services(json.dumps({"postgres": credentials}))["postgres"], credentials)
        with self.assertRaises(DeployError):
            parse_services('{"postgres": {"user": "app"}}')

    def test_corrupt_service_json_never_regenerates_credentials(self):
        for raw in ("broken", "[]", "null", '{"postgres":null}'):
            with self.subTest(raw=raw), self.assertRaises(DeployError):
                parse_services(raw)
        self.assertEqual(parse_services(None), {})

    def test_invalid_repo_queries_and_hosts(self):
        for url in ("https:///app.git", "https://github.com/u/app.git?token=secret", "https://[broken/app.git"):
            with self.subTest(url=url), self.assertRaises(DeployError):
                validate_repo_url(url)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "last.json"
        self.addCleanup(patch.stopall)
        patch.object(config, "CONFIG_DIR", Path(self.directory.name)).start()
        patch.object(config, "LAST_DEPLOY_FILE", self.path).start()

    def test_atomic_private_profile_with_no_token(self):
        config.save_last(config.DeployProfile("host", repo="https://TOKEN@github.com/u/app.git"))
        self.assertNotIn("TOKEN", self.path.read_text())
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config.load_last().repo, "https://github.com/u/app.git")
        self.assertEqual(list(self.path.parent.glob(".profile-*")), [])

    def test_old_token_profile_is_migrated(self):
        self.path.write_text('{"host":"host","repo":"https://TOKEN@github.com/u/app.git"}')
        self.assertNotIn("TOKEN", config.load_last().repo)
        self.assertNotIn("TOKEN", self.path.read_text())

    def test_corrupt_profiles(self):
        for raw in ("[]", "null", "broken", '{"host":2,"repo":"url"}', '{"host":"h","repo":"r","port":0}'):
            self.path.write_text(raw)
            self.assertIsNone(config.load_last())


class SSHTests(unittest.TestCase):
    def test_atomic_file_write_with_shell_quoting(self):
        class LocalSFTP:
            def file(self, path, mode):
                return open(path, "x", encoding="utf-8")
            def chmod(self, path, mode):
                os.chmod(path, mode)
            def remove(self, path):
                os.unlink(path)
            def close(self):
                pass
        ssh = SSHClient(ServerCredentials("host"))
        ssh._client = MagicMock()
        ssh._client.open_sftp.return_value = LocalSFTP()
        def run(command, **kwargs):
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
            if result.returncode:
                raise RemoteCommandError(command, result.returncode, result.stderr)
        with tempfile.TemporaryDirectory() as directory, patch.object(ssh, "run", side_effect=run):
            destination = Path(directory) / "a 'quoted $name.env"
            destination.write_text("old")
            ssh.write_file(str(destination), "new secret", mode="600")
            self.assertEqual(destination.read_text(), "new secret")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob("*.abzctl-*")), [])
            # A directory destination must not silently consume the staged file.
            with self.assertRaises(RemoteCommandError):
                ssh.write_file(directory, "secret", mode="600")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_connect_is_idempotent_and_rejects_unknown_keys(self):
        with patch("abzctl.core.ssh_client.paramiko.SSHClient") as factory:
            connection = factory.return_value
            connection.get_transport.return_value.is_active.return_value = True
            connection.get_transport.return_value.is_authenticated.return_value = True
            ssh = SSHClient(ServerCredentials("host", password="secret"))
            ssh.connect()
            with ssh:
                self.assertEqual(connection.connect.call_count, 1)
            self.assertIsInstance(connection.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
            connection.close.assert_called_once()

    def test_failed_connection_is_closed(self):
        with patch("abzctl.core.ssh_client.paramiko.SSHClient") as factory:
            connection = factory.return_value
            connection.connect.side_effect = paramiko.SSHException("unknown host")
            connection.get_transport.return_value.is_authenticated.return_value = False
            with self.assertRaises(SSHConnectionError):
                SSHClient(ServerCredentials("host")).connect()
            connection.close.assert_called_once()

    def test_secret_upload_permissions_precede_write(self):
        ssh = SSHClient(ServerCredentials("host"))
        ssh._client = MagicMock()
        sftp = ssh._client.open_sftp.return_value
        events = []
        sftp.chmod.side_effect = lambda *args: events.append("chmod")
        sftp.file.return_value.__enter__.return_value.write.side_effect = lambda content: events.append("write")
        with patch.object(ssh, "run"):
            ssh.write_file("/app/.env", "TOKEN=secret", mode="600")
        self.assertEqual(events, ["chmod", "write"])
        self.assertEqual(sftp.file.call_args.args[1], "wx")
        sftp.remove.assert_called_once()
        sftp.close.assert_called_once()

    def test_failed_upload_is_cleaned(self):
        ssh = SSHClient(ServerCredentials("host"))
        ssh._client = MagicMock()
        sftp = ssh._client.open_sftp.return_value
        sftp.file.return_value.__enter__.return_value.write.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            ssh.write_file("/app/.env", "secret")
        sftp.remove.assert_called_once()
        sftp.close.assert_called_once()


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.console_patch = patch.object(ui, "console", Console(file=io.StringIO()))
        self.console_patch.start()
        self.addCleanup(self.console_patch.stop)

    def test_empty_provided_required_value_fails(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = ("kod", [EnvVar("BOT_TOKEN", "", True, True)])
        with self.assertRaises(DeployError):
            main._fill_env(analyzer, {"BOT_TOKEN": ""})
        analyzer.write_env.assert_not_called()

    def test_env_variable_supplies_secret(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = ("kod", [EnvVar("BOT_TOKEN", "", True, True)])
        with patch.dict(os.environ, {"ABZCTL_ENV_BOT_TOKEN": "token"}):
            main._fill_env(analyzer, {})
        analyzer.write_env.assert_called_once_with({"BOT_TOKEN": "token"})

    def test_final_docker_stage_controls_port(self):
        ssh = MagicMock()
        ssh.read_file.return_value = "FROM node AS build\nEXPOSE 8080\nFROM nginx\nEXPOSE 80\n"
        analyzer = ProjectAnalyzer(ssh, SimpleNamespace(project_dir="/app"))
        self.assertEqual(analyzer._exposed_port("Dockerfile"), 80)

    def test_required_env_fails_before_writing(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = ("kod", [EnvVar("BOT_TOKEN", "", True, True)])
        analyzer.suggest_value.side_effect = ProjectAnalyzer.suggest_value
        with patch.object(main, "_interactive", return_value=False), self.assertRaises(DeployError):
            main._fill_env(analyzer, {})
        analyzer.write_env.assert_not_called()

    def test_provided_env_avoids_prompt(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = ("kod", [EnvVar("BOT_TOKEN", "", True, True)])
        with patch.object(main.Prompt, "ask", side_effect=AssertionError("unexpected prompt")):
            main._fill_env(analyzer, {"BOT_TOKEN": "token"})
        analyzer.write_env.assert_called_once_with({"BOT_TOKEN": "token"})

    def test_existing_env_explicit_override_preserves_content(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = (None, [])
        main._fill_env(analyzer, {"BOT_TOKEN": "new"}, {"BOT_TOKEN": "new"})
        analyzer.write_env.assert_called_once_with({"BOT_TOKEN": "new"}, preserve=True)

    def test_secret_prompt_is_masked(self):
        analyzer = MagicMock()
        analyzer.missing_env.return_value = ("kod", [EnvVar("BOT_TOKEN", "", True, True)])
        with patch.object(main, "_interactive", return_value=True), patch.object(main.Prompt, "ask", return_value="secret") as prompt:
            main._fill_env(analyzer, {})
        self.assertTrue(prompt.call_args.kwargs["password"])

    def test_yes_disables_all_prompts_and_restores_context(self):
        with patch.object(main.sys.stdin, "isatty", return_value=True):
            with patch.object(main, "_run_deploy_inner", side_effect=lambda options: self.assertFalse(main._interactive())):
                main._run_deploy(main.DeployOptions(yes=True))
            self.assertTrue(main._interactive())


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.console_patch = patch.object(ui, "console", Console(file=io.StringIO()))
        self.console_patch.start()
        self.addCleanup(self.console_patch.stop)

    def test_ambiguous_port_is_not_chosen_in_ci(self):
        deployer = MagicMock()
        deployer.published_ports.return_value = [("db", 5432), ("web", 8080)]
        with patch.object(main, "_interactive", return_value=False), self.assertRaises(DeployError):
            main._detect_port(main.DeployOptions(), deployer, "compose.yaml")

    def test_compose_validates_before_starting(self):
        ssh = MagicMock()
        ssh.run.side_effect = RemoteCommandError("config", 1)
        with self.assertRaises(RemoteCommandError):
            Deployer(ssh, "https://github.com/u/app.git").compose_up("compose.yaml")
        self.assertEqual(ssh.run.call_count, 1)
        self.assertIn("config --quiet", ssh.run.call_args.args[0])
    def test_failed_health_never_saves_profile_or_updates_caddy(self):
        self._pipeline(False)

    def test_successful_deploy_saves_profile(self):
        self._pipeline(True)

    def _pipeline(self, healthy):
        with patch.object(main.config, "load_last", return_value=None), patch.object(main.config, "save_last") as save, patch.object(main, "_connect") as connect, patch.object(main, "Provisioner") as provision, patch.object(main, "Deployer") as deploy, patch.object(main, "ProjectAnalyzer") as analyze, patch.object(main, "CaddyManager") as caddy, patch.object(main, "_print_result"):
            ssh = connect.return_value
            ssh.__enter__.return_value = ssh
            provision.return_value.detect_os.return_value.pretty_name = "Debian"
            analyzer = analyze.return_value
            analyzer.detect.return_value = ProjectPlan("compose", "Docker Compose", "compose.yaml")
            analyzer.missing_env.return_value = (None, [])
            deploy.return_value.current_commit.return_value = "abc"
            deploy.return_value.wait_for_app.return_value = healthy
            deploy.return_value.compose_logs.return_value = "failed to start"
            options = main.DeployOptions(host="host", password="pw", repo="https://github.com/u/app.git", app_port=3000, skip_provision=True, yes=True)
            if healthy:
                main._run_deploy(options)
                save.assert_called_once()
            else:
                with self.assertRaises(DeployError):
                    main._run_deploy(options)
                save.assert_not_called()
                caddy.assert_not_called()

    def test_http_timeout_does_not_count_as_healthy(self):
        ssh = MagicMock()
        ssh.run.return_value = CommandResult("curl", 28, "200", "timeout")
        self.assertFalse(Deployer(ssh, "https://github.com/u/app.git").wait_for_app(3000, attempts=1, delay=0))

    def test_cli_exit_and_env_validation(self):
        runner = CliRunner()
        self.assertEqual(runner.invoke(main.app, ["deploy", "-y"]).exit_code, 1)
        self.assertEqual(runner.invoke(main.app, ["deploy", "--env", "bad"]).exit_code, 2)
        with patch.object(main, "_run_deploy") as deploy:
            result = runner.invoke(main.app, ["deploy", "--env", "A=x=y", "--env", "B=z", "-y"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(deploy.call_args.args[0].env, {"A": "x=y", "B": "z"})


class CaddyTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_preserves_other_sites(self):
        content = "other.example.com {\n respond 200\n}\n"
        updated = upsert_block(content, "app.example.com", 3000)
        self.assertTrue(updated.startswith(content))
        self.assertEqual(upsert_block(updated, "app.example.com", 3000), updated)
        self.assertIn("localhost:4000", upsert_block(updated, "app.example.com", 4000))

    def test_conflict_in_second_site_address(self):
        self.assertTrue(find_unmanaged_conflict("other.example.com, app.example.com {\n}\n", "app.example.com"))
        self.assertFalse(find_unmanaged_conflict(upsert_block("", "app.example.com", 3000), "app.example.com"))

    def test_empty_config_restored_on_reload_failure(self):
        ssh = MagicMock()
        ssh.read_file.return_value = ""
        manager = CaddyManager(ssh)
        with patch.object(manager, "reload", side_effect=RemoteCommandError("reload", 1)):
            with self.assertRaises(CaddyError):
                manager.configure_site("app.example.com", 3000)
        self.assertEqual(ssh.write_file.call_args.args, (CADDYFILE, ""))


class ComposeIntegrationTests(unittest.TestCase):
    def test_generated_compose_and_env_parse_with_real_compose(self):
        if not shutil.which("docker"):
            self.skipTest("Docker Compose unavailable")
        version = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if version.returncode:
            self.skipTest("Docker Compose unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".abzctl").mkdir()
            class LocalSSH:
                def write_file(self, name, content, **kwargs):
                    Path(name).write_text(content)
                def read_file(self, name):
                    return Path(name).read_text() if Path(name).exists() else None
                def run(self, command, **kwargs):
                    return CommandResult(command, 0, "", "")
            analyzer = ProjectAnalyzer(LocalSSH(), SimpleNamespace(project_dir=directory))
            analyzer._prepare_work_dir = lambda: None
            analyzer._exists = lambda name: (path / name).is_file()
            values = {"TOKEN": "abc$HOME${UNDEFINED}", "QUOTE": "a'b\"c # x", "SLASH": "end\\", "MULTILINE": "line1\nline2", "BACKSLASH": "a\\ntext"}
            analyzer.write_env(values)
            analyzer.write_env({"TOKEN": "updated$TOKEN", "ADDED": "kept"}, preserve=True)
            values.update({"TOKEN": "updated$TOKEN", "ADDED": "kept"})
            for worker in (False, True):
                with self.subTest(worker=worker):
                    services = {kind: new_credentials(kind, "app") for kind in ("postgres", "mysql", "mongo", "redis")}
                    analyzer.write_compose(ProjectPlan("railpack", "Python"), 3000, False, image="busybox", services=services, worker=worker)
                    result = subprocess.run(["docker", "compose", "-p", "test-app", "-f", str(path / ".abzctl/compose.yaml"), "config", "--format", "json"], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    app = json.loads(result.stdout)["services"]["app"]
                    # Compose config escapes literal $ for serialization.
                    self.assertEqual({k: app["environment"][k].replace("$$", "$") for k in values}, values)
                    self.assertEqual(bool(app.get("ports")), not worker)
                    if not worker:
                        self.assertEqual(app["ports"][0]["host_ip"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()

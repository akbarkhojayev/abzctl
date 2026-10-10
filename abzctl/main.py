"""abzctl CLI kirish nuqtasi.

Asosiy foydalanish — bitta buyruq:

    abzctl               — qadam-baqadam so'raydi va loyihani deploy qiladi

Qo'shimcha buyruqlar:

    abzctl deploy        — xuddi shu, lekin qiymatlarni flaglar orqali berish mumkin (CI uchun)
    abzctl check-server  — serverga ulanib, holatini ko'rsatadi
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Annotated, Dict, Optional

import paramiko
import typer
from rich.markup import escape
from rich.prompt import Confirm, IntPrompt, Prompt

from abzctl import __version__, config, ui
from abzctl.core import services as svc
from abzctl.core.analyzer import ProjectAnalyzer
from abzctl.core.caddy import CaddyManager, check_dns, resolve_host, validate_domain
from abzctl.core.deployer import Deployer, project_name_from_repo, validate_repo_url
from abzctl.core.provisioner import Provisioner
from abzctl.core.ssh_client import ServerCredentials, SSHClient
from abzctl.exceptions import AbzctlError, SSHAuthError

app = typer.Typer(
    name="abzctl",
    help="🚀 Loyihani serverga deploy qilish. Shunchaki [bold]abzctl[/] deb yozing — kerakli narsalar so'raladi.",
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["--help"]},  # -h --host uchun band
)

DEFAULT_KEYS = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa", "~/.ssh/id_ecdsa")
MAX_AUTH_ATTEMPTS = 3


@dataclass
class DeployOptions:
    """Deploy uchun barcha parametrlar. None — hali ma'lum emas (so'raladi yoki aniqlanadi)."""

    host: Optional[str] = None
    user: str = "root"
    port: int = 22
    password: Optional[str] = None
    key_file: Optional[str] = None
    key_passphrase: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    name: Optional[str] = None
    app_port: Optional[int] = None
    domain: Optional[str] = None
    skip_provision: bool = False
    yes: bool = False
    verbose: bool = False


# ============================================================================ yordamchilar


def _interactive() -> bool:
    return sys.stdin.isatty()


def _handle_errors(func) -> None:
    """Buyruqni bajaradi va abzctl xatolarini rich paneli ko'rinishida chiqaradi."""
    try:
        func()
    except AbzctlError as exc:
        ui.console.print()
        ui.error_panel(exc.title, exc.message, exc.hint)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        ui.console.print()
        ui.warn("To'xtatildi.")
        raise typer.Exit(code=130)


def _default_key() -> Optional[str]:
    """Kompyuterda mavjud bo'lgan birinchi standart SSH kalitni topadi."""
    return next((k for k in DEFAULT_KEYS if os.path.isfile(os.path.expanduser(k))), None)


def _local_git_remote() -> Optional[str]:
    """Joriy papka git repo bo'lsa, uning `origin` havolasini qaytaradi.

    `git@github.com:user/app.git` ko'rinishidagi havola HTTPS'ga aylantiriladi, chunki
    yangi serverda GitHub uchun SSH kalit odatda bo'lmaydi (ochiq repolar HTTPS bilan ishlaydi).
    """
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = res.stdout.strip()
    if res.returncode != 0 or not url:
        return None
    match = re.match(r"^git@([^:]+):(.+)$", url)
    if match:
        url = f"https://{match.group(1)}/{match.group(2)}"
    return url


def _ask(question: str, default: Optional[str] = None) -> str:
    """Matn so'raydi. Bo'sh javob qabul qilinmaydi (default bo'lsa, Enter uni tanlaydi)."""
    while True:
        value = Prompt.ask(question, default=default) if default else Prompt.ask(question)
        if value and value.strip():
            return value.strip()
        ui.warn("Bo'sh bo'lmasligi kerak.")


def _ask_until_valid(question: str, validator, default: Optional[str] = None) -> str:
    """Qiymat to'g'ri kiritilmaguncha qayta so'raydi."""
    while True:
        value = _ask(question, default)
        try:
            return validator(value.strip())
        except AbzctlError as exc:
            ui.warn(exc.message)


# ============================================================================ 1. server


def _ask_server(o: DeployOptions, last: Optional[config.DeployProfile]) -> None:
    """Server manzili va kirish usulini so'raydi (faqat berilmaganlarini)."""
    if not o.host:
        if not _interactive():
            raise AbzctlError("--host berilmagan.", hint="Masalan: abzctl deploy --host 203.0.113.10")
        o.host = _ask("Server IP manzili", default=last.host if last else None)
        o.user = _ask("Foydalanuvchi", default=last.user if last else o.user)
        o.port = IntPrompt.ask("SSH port", default=last.port if last else o.port)

    if o.key_file or o.password:
        return
    if not _interactive():
        raise AbzctlError(
            "Parol ham, SSH kalit ham berilmagan.",
            hint="--key-file bering yoki parolni ABZCTL_PASSWORD muhit o'zgaruvchisi orqali bering.",
        )

    key = (last.key_file if last else None) or _default_key()
    if key:
        ui.info(f"Kompyuteringizda SSH kalit bor: [bold]{key}[/]")
        method = Prompt.ask("Qanday kirasiz?", choices=["kalit", "parol"], default="kalit")
    else:
        method = "parol"
    if method == "kalit":
        o.key_file = _ask("SSH kalit yo'li", default=key)
    else:
        o.password = Prompt.ask(f"{o.user}@{o.host} paroli [dim](ko'rinmaydi)[/]", password=True)


def _connect(o: DeployOptions) -> SSHClient:
    """Serverga ulanadi. Interaktiv rejimda xato parol/kalitda qayta so'raydi."""
    for attempt in range(1, MAX_AUTH_ATTEMPTS + 1):
        creds = ServerCredentials(
            host=o.host, user=o.user, port=o.port, password=o.password,
            key_file=o.key_file, key_passphrase=o.key_passphrase,
        )
        ssh = SSHClient(creds)
        try:
            with ui.step(f"Serverga ulanish: {o.user}@{o.host}:{o.port}", o.verbose):
                ssh.connect()
            return ssh
        except SSHAuthError as exc:
            if not _interactive() or attempt == MAX_AUTH_ATTEMPTS:
                raise
            if isinstance(exc.__cause__, paramiko.PasswordRequiredException):
                o.key_passphrase = Prompt.ask("SSH kalit paroli (passphrase)", password=True)
                continue
            ui.warn(exc.message)
            if o.key_file and not Confirm.ask("Parol bilan kirib ko'ramizmi?", default=True):
                raise
            o.key_file = None
            o.password = Prompt.ask(f"{o.user}@{o.host} paroli [dim](ko'rinmaydi)[/]", password=True)
    raise AssertionError("unreachable")


# ============================================================================ 2. loyiha


def _ask_project(o: DeployOptions, last: Optional[config.DeployProfile]) -> None:
    """Repo havolasini so'raydi (berilmagan bo'lsa)."""
    if not o.repo:
        if not _interactive():
            raise AbzctlError("--repo berilmagan.")
        suggestion = _local_git_remote() or (last.repo if last else None)

        def check_repo(value: str) -> str:
            validate_repo_url(value)
            return value

        o.repo = _ask_until_valid("GitHub/GitLab repo havolasi", check_repo, default=suggestion)
    else:
        o.repo = o.repo.strip()
        validate_repo_url(o.repo)

    if o.domain:
        o.domain = validate_domain(o.domain)


def _ask_domain(o: DeployOptions, last: Optional[config.DeployProfile]) -> None:
    """Web-ilova uchun domen so'raydi (loyiha tahlilidan keyin; bot uchun so'ralmaydi)."""
    had_domain = bool(last and last.domain)
    if Confirm.ask("Domen ulaymizmi? [dim](HTTPS bilan, masalan app.example.com)[/]", default=had_domain):
        o.domain = _ask_until_valid("Domen", validate_domain, default=last.domain if had_domain else None)


def _show_plan(o: DeployOptions, project: str) -> None:
    ui.console.print()
    ui.console.print(
        ui.summary_table(
            "Deploy rejasi",
            [
                ("Server", f"{o.user}@{o.host}:{o.port}"),
                ("Repo", o.repo + (f" [dim]({o.branch})[/]" if o.branch else "")),
                ("Serverdagi papka", f"/var/www/{project}"),
                ("Port", str(o.app_port) if o.app_port else "[dim]avtomatik aniqlanadi[/]"),
                ("Manzil", f"https://{o.domain}" if o.domain else "[dim]loyiha tahlilidan keyin aniqlanadi[/]"),
            ],
        )
    )


# ============================================================================ 3. deploy


def _detect_port(o: DeployOptions, deployer: Deployer, compose_file: str) -> int:
    """Ilova portini compose faylidan aniqlaydi, kerak bo'lsa foydalanuvchidan so'raydi."""
    if o.app_port:
        return o.app_port

    found = deployer.published_ports(compose_file)
    ports = sorted({port for _, port in found})
    if len(ports) == 1:
        services = ", ".join(sorted({svc for svc, _ in found}))
        ui.info(f"Port aniqlandi: [bold]{ports[0]}[/] ({services} servisi)")
        return ports[0]

    if len(ports) > 1:
        listing = ", ".join(f"{svc}:{port}" for svc, port in found)
        ui.info(f"Compose faylida bir nechta port bor: {listing}")
        if _interactive():
            return int(Prompt.ask("Qaysi port asosiy ilova?", choices=[str(p) for p in ports], default=str(ports[0])))
        ui.warn(f"{ports[0]} tanlandi. Boshqasi kerak bo'lsa --app-port bering.")
        return ports[0]

    ui.warn(
        "Compose faylida tashqariga ochilgan port topilmadi. Ilova ishlashi uchun compose'da "
        "masalan [bold]ports: [\"127.0.0.1:3000:3000\"][/] bo'lishi kerak."
    )
    if _interactive():
        return IntPrompt.ask("Ilova porti", default=3000)
    return 3000


def _choose_services(analyzer: ProjectAnalyzer) -> Dict[str, Dict[str, str]]:
    """Loyihaga kerakli bazalarni aniqlaydi va foydalanuvchidan tasdiq oladi.

    Oldingi deploy'da qabul qilingan yoki rad etilgan bazalar qayta so'ralmaydi.
    """
    saved = analyzer.saved_services()
    enabled = [kind for kind, data in saved.items() if not data.get("disabled")]
    declined = [kind for kind, data in saved.items() if data.get("disabled")]

    for found in analyzer.detect_services():
        if found.kind in saved:
            continue
        if _interactive():
            question = f"Loyihaga [bold]{found.label}[/] kerak ko'rinadi [dim]({found.reason})[/]. Serverda ishga tushiraymi?"
            accepted = Confirm.ask(question, default=True)
        else:
            ui.info(f"{found.label} kerak ko'rinadi ({found.reason}) — avtomatik qo'shiladi.")
            accepted = True
        (enabled if accepted else declined).append(found.kind)

    services = analyzer.resolve_services(enabled, declined)
    if services:
        labels = ", ".join(svc.SERVICE_LABELS[k] for k in svc.SERVICE_ORDER if k in services)
        ui.success(f"Bazalar: [bold]{labels}[/] [dim](parollar avtomatik yaratildi, ma'lumotlar saqlanib qoladi)[/]")
    return services


def _fill_env(analyzer: ProjectAnalyzer, provided: Dict[str, str]) -> None:
    """`.env.example` bo'lsa va serverda `.env` hali yo'q bo'lsa, qiymatlarni so'rab `.env` yaratadi.

    `provided` — abzctl o'zi beradigan o'zgaruvchilar (masalan DATABASE_URL): ular so'ralmaydi.
    """
    example, variables = analyzer.missing_env()
    if not example:
        return
    if not variables:
        analyzer.write_env({})
        return

    values = {var.name: provided[var.name] for var in variables if var.name in provided}
    variables = [var for var in variables if var.name not in provided]
    if values:
        ui.info(f"{', '.join(values)} — bazaga ulanish uchun abzctl o'zi beradi.")
    source = "Kodda sozlamalar ishlatilgan." if example == "kod" else f"Loyihada [bold]{example}[/] bor."
    if variables and _interactive():
        ui.info(f"{source} Qiymatlarni kiriting [dim](Enter — taklif qilingan qiymat)[/]:")
        for var in variables:
            suggestion = analyzer.suggest_value(var)
            if var.required and not suggestion:
                # BOT_TOKEN kabi qiymatsiz ishlamaydigan sozlama — bo'sh qoldirib bo'lmaydi.
                values[var.name] = _ask(f"  {var.name} [dim](majburiy)[/]")
                continue
            hint = " [dim](avtomatik yaratildi)[/]" if suggestion != var.default else ""
            values[var.name] = Prompt.ask(f"  {var.name}{hint}", default=suggestion, show_default=not var.secret)
    elif variables:
        values.update({var.name: analyzer.suggest_value(var) for var in variables})
        missing = [var.name for var in variables if var.required and not values[var.name]]
        if missing:
            ui.warn(f"Qiymati kiritilmagan: {', '.join(missing)}. Serverda {analyzer.dir}/.env faylini to'ldiring.")
        else:
            ui.warn("Taklif qilingan qiymatlar ishlatildi. Kerak bo'lsa serverda .env faylini tahrirlang.")
    # Koddagi default bilan bir xil qiymatni .env'ga yozmaymiz — kod o'z default'ini ishlatadi.
    code_defaults = {var.name: var.default for var in variables if var.from_code and var.default}
    values = {k: v for k, v in values.items() if code_defaults.get(k) != v}
    analyzer.write_env(values)
    ui.success(".env yaratildi (keyingi deploy'larda o'zgarmaydi).")


def _run_deploy(o: DeployOptions) -> None:
    """To'liq deploy jarayoni: so'rash → ulanish → o'rnatish → deploy → domen."""
    ui.print_banner()

    # --- Oxirgi deploy'ni taklif qilish
    last = config.load_last()
    reuse = False
    if last and _interactive() and not o.host and not o.repo:
        target = f"https://{last.domain}" if last.domain else f"{last.host}:{last.app_port or '?'}"
        project_name = last.name or project_name_from_repo(last.repo)
        ui.info(f"Oxirgi deploy: [bold]{project_name}[/] → {last.user}@{last.host} ({target})")
        if Confirm.ask("Shu loyihani yangilaymizmi?", default=True):
            reuse = True
            o.host, o.user, o.port, o.key_file = last.host, last.user, last.port, last.key_file
            o.repo, o.branch, o.name = last.repo, last.branch, last.name
            o.app_port, o.domain = last.app_port, last.domain

    # --- 1. Server
    ui.section("1. Server")
    _ask_server(o, last)
    ssh = _connect(o)

    with ssh:
        provisioner = Provisioner(ssh)
        with ui.step("Serverni tekshirish", o.verbose):
            os_info = provisioner.detect_os()
            provisioner.check_privileges()
        ui.info(f"{os_info.pretty_name} · {'root' if ssh.is_root else o.user + ' (sudo)'}")

        # --- 2. Loyiha
        ui.section("2. Loyiha")
        _ask_project(o, last)
        project = o.name or project_name_from_repo(o.repo)
        _show_plan(o, project)
        if not o.yes and _interactive() and not Confirm.ask("\nBoshlaymizmi?", default=True):
            ui.warn("Bekor qilindi.")
            raise typer.Exit()

        # --- 3. Serverga kerakli dasturlarni o'rnatish
        ui.section("3. Kerakli dasturlar")
        if o.skip_provision:
            ui.info("O'tkazib yuborildi (--skip-provision).")
        else:
            with ui.step("curl, git", o.verbose) as s:
                installed = provisioner.ensure_base_packages(on_line=s.on_line)
            if installed:
                ui.info(f"O'rnatildi: {', '.join(installed)}")
            with ui.step("Docker", o.verbose) as s:
                docker_new = provisioner.ensure_docker(on_line=s.on_line)
            if docker_new:
                ui.info("Docker o'rnatildi.")
            with ui.step("Docker Compose", o.verbose) as s:
                provisioner.ensure_compose(on_line=s.on_line)
            swap = provisioner.ensure_swap()
            if swap:
                ui.info(f"Server RAM'i kam — build yiqilmasligi uchun {swap} swap qo'shildi.")

        # --- 4. Loyihani tanib olish va ishga tushirish
        ui.section("4. Loyihani tanib olish")
        deployer = Deployer(ssh, o.repo, branch=o.branch, project_name=project)
        with ui.step("Kodni yuklab olish", o.verbose) as s:
            deployer.prepare_directory()
            action = deployer.sync_code(on_line=s.on_line)
        commit = deployer.current_commit()
        ui.info(f"{'Klonlandi' if action == 'cloned' else 'Yangilandi'}: [bold]{commit}[/]")

        analyzer = ProjectAnalyzer(ssh, deployer)
        plan = analyzer.detect()
        if plan.kind == "railpack":
            if not o.skip_provision:
                with ui.step("Build vositalari (Railpack, buildx)", o.verbose) as s:
                    provisioner.ensure_buildx(on_line=s.on_line)
                    provisioner.ensure_railpack(on_line=s.on_line)
            packages = analyzer.ensure_python_requirements()
            if packages is not None:
                ui.info(
                    "requirements.txt yo'q — importlardan yaratildi: "
                    f"[bold]{', '.join(packages) or '(tashqi kutubxona yo‘q)'}[/]"
                )
            with ui.step("Loyiha turini aniqlash", o.verbose) as s:
                plan = analyzer.railpack_prepare(on_line=s.on_line)
        extra = f" [dim]({', '.join(plan.details)})[/]" if plan.details and plan.kind == "railpack" else ""
        ui.success(f"Loyiha turi: [bold]{plan.label}[/]{extra}")

        worker = analyzer.worker_library() if plan.generated else None
        if worker:
            ui.success(f"Bu bot / fon jarayoni ([bold]{worker}[/]) — port va domen kerak emas.")
            if o.domain:
                ui.warn("Bot uchun domen kerak emas — domen e'tiborsiz qoldirildi.")
                o.domain = None
            o.app_port = None
        elif not o.domain and not reuse and _interactive():
            _ask_domain(o, last)

        services = _choose_services(analyzer) if plan.generated else {}
        _fill_env(analyzer, provided=svc.app_environment(services))

        ui.section("5. Ishga tushirish")
        if plan.generated:
            if not worker:
                o.app_port = o.app_port or analyzer.previous_port() or analyzer.free_port(plan.container_port or 3000)
                if plan.kind == "dockerfile" and not plan.container_port:
                    ui.warn(f"Dockerfile'da EXPOSE yo'q — ilova PORT={o.app_port} o'zgaruvchisini tinglaydi deb hisoblandi.")
            image = None
            if plan.kind == "railpack":
                with ui.step("Build (Railpack)", o.verbose) as s:
                    image = analyzer.railpack_build(on_line=s.on_line)
            analyzer.write_compose(
                plan, o.app_port or 0, public=not o.domain, image=image, services=services, worker=bool(worker)
            )
            if o.app_port:
                ui.info(f"Port: [bold]{o.app_port}[/]")
        else:
            o.app_port = _detect_port(o, deployer, plan.compose_file)

        with ui.step("Ishga tushirish (docker compose up)", o.verbose) as s:
            deployer.compose_up(plan.compose_file, on_line=s.on_line)
        if worker:
            with ui.step("Bot ishlab turganini tekshirish", o.verbose) as s:
                healthy = deployer.wait_for_running(plan.compose_file)
                if not healthy:
                    s.fail()
            problem = "Bot ishga tushdi, lekin to'xtab qolyapti."
        else:
            with ui.step("Ilova javob berishini kutish", o.verbose) as s:
                healthy = deployer.wait_for_app(o.app_port)
                if not healthy:
                    s.fail()
            problem = f"Ilova {o.app_port}-portda javob bermadi."
        if not healthy:
            logs = deployer.compose_logs(plan.compose_file)
            ui.warn(f"{problem} Konteyner loglari:")
            if logs:
                ui.console.print(f"[dim]{escape(logs)}[/]")
            if re.search(r"Unauthorized|InvalidToken|TokenValidationError|401", logs):
                ui.notice_panel(
                    "💡 Token noto'g'ri",
                    "Telegram tokenni rad etdi. Tokenni @BotFather dan qayta oling va serverda tuzating:\n\n"
                    f"    [bold]nano {deployer.project_dir}/.env[/]\n\n"
                    "So'ng yana [bold]abzctl[/] deb yozing.",
                )

        # --- 6. Domen
        if o.domain:
            ui.section("6. Domen va HTTPS")
            if not o.skip_provision:
                with ui.step("Caddy (HTTPS)", o.verbose) as s:
                    caddy_source = provisioner.ensure_caddy(on_line=s.on_line)
                if caddy_source:
                    ui.info(f"Caddy o'rnatildi ({caddy_source}).")
            with ui.step("Firewall: 80/443 portlari", o.verbose) as s:
                provisioner.open_firewall_ports(on_line=s.on_line)
            with ui.step(f"Caddy: {o.domain} → {o.app_port}", o.verbose):
                CaddyManager(ssh).configure_site(o.domain, o.app_port)

    # Keyingi safar qayta so'ramaslik uchun (parol saqlanmaydi).
    config.save_last(
        config.DeployProfile(
            host=o.host, user=o.user, port=o.port, key_file=o.key_file, repo=o.repo,
            branch=o.branch, name=o.name, app_port=o.app_port, domain=o.domain,
        )
    )
    _print_result(o, project, commit, healthy, services, deployer.project_dir)


def _print_result(
    o: DeployOptions, project: str, commit: str, healthy: bool,
    services: Dict[str, Dict[str, str]], project_dir: str,
) -> None:
    ui.console.print()
    lines = [f"Loyiha:  [bold]{project}[/]", f"Commit:  {commit}"]
    if services:
        labels = ", ".join(svc.SERVICE_LABELS[k] for k in svc.SERVICE_ORDER if k in services)
        lines.append(f"Bazalar: {labels} [dim](parollar: {project_dir}/.abzctl/services.json)[/]")

    if o.domain:
        server_ip = resolve_host(o.host)
        dns_ips = check_dns(o.domain)
        lines.append(f"Manzil:  [bold cyan]https://{o.domain}[/]")
        if dns_ips and server_ip in dns_ips:
            lines.append("\n[green]✔ DNS to'g'ri sozlangan — SSL sertifikat avtomatik olinadi.[/]")
        else:
            lines += [
                "\n[yellow]⚠ Oxirgi qadam: domen provayderingizda A-rekord yarating:[/]",
                f"    [bold]{o.domain}[/]  →  [bold]{server_ip}[/]",
                "[dim]DNS yangilangach (bir necha daqiqa), sayt HTTPS bilan o'zi ochiladi.[/]",
            ]
    elif o.app_port:
        lines.append(f"Manzil:  [bold cyan]http://{o.host}:{o.app_port}[/]")
    else:
        if healthy:
            lines.append("Holat:   [bold green]bot ishlayapti[/] — server qayta yoqilsa ham o'zi ishga tushadi")
        else:
            lines.append("Holat:   [bold red]bot to'xtab qolyapti[/] — yuqoridagi loglarni ko'ring")
        lines.append(f"[dim]Loglar: cd {project_dir}/.abzctl && sudo docker compose -p {project} logs -f[/]")

    lines.append("\n[dim]Yangilash uchun yana [bold]abzctl[/bold] deb yozing.[/]")
    ui.notice_panel(
        "🚀 Tayyor!" if healthy else "⚠ Deploy tugadi, lekin ilova ishlamayapti",
        "\n".join(lines),
        style="green" if healthy else "yellow",
    )


# ============================================================================ buyruqlar


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"abzctl v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Versiyani ko'rsatish."),
    ] = None,
) -> None:
    """Hech qanday buyruqsiz [bold]abzctl[/] — qadam-baqadam deploy."""
    if ctx.invoked_subcommand is None:
        _handle_errors(lambda: _run_deploy(DeployOptions()))


HostOpt = Annotated[Optional[str], typer.Option("--host", "-h", help="Server IP manzili.")]
UserOpt = Annotated[str, typer.Option("--user", "-u", help="SSH foydalanuvchi.")]
PortOpt = Annotated[int, typer.Option("--port", "-p", min=1, max=65535, help="SSH port.")]
PasswordOpt = Annotated[
    Optional[str],
    typer.Option("--password", envvar="ABZCTL_PASSWORD", help="Server paroli (sudo uchun ham).", show_default=False),
]
KeyFileOpt = Annotated[Optional[str], typer.Option("--key-file", "-k", help="SSH kalit yo'li.")]
PassphraseOpt = Annotated[
    Optional[str],
    typer.Option("--key-passphrase", envvar="ABZCTL_KEY_PASSPHRASE", help="Kalit paroli.", show_default=False),
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Serverdagi buyruqlar chiqishini to'liq ko'rsatish.")]


@app.command()
def deploy(
    host: HostOpt = None,
    user: UserOpt = "root",
    port: PortOpt = 22,
    password: PasswordOpt = None,
    key_file: KeyFileOpt = None,
    key_passphrase: PassphraseOpt = None,
    repo: Annotated[Optional[str], typer.Option("--repo", "-r", help="Git repo havolasi.")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", "-b", help="Git branch.")] = None,
    name: Annotated[Optional[str], typer.Option("--name", help="Loyiha nomi (/var/www/<nom>).")] = None,
    app_port: Annotated[Optional[int], typer.Option("--app-port", min=1, max=65535, help="Ilova porti (default: compose'dan aniqlanadi).")] = None,
    domain: Annotated[Optional[str], typer.Option("--domain", "-d", help="Domen (HTTPS bilan).")] = None,
    skip_provision: Annotated[bool, typer.Option("--skip-provision", help="Dasturlarni tekshirmaslik.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Tasdiq so'ramaslik.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Flaglar bilan deploy (CI/skriptlar uchun). Berilmagan qiymatlar so'raladi."""
    options = DeployOptions(
        host=host, user=user, port=port, password=password, key_file=key_file,
        key_passphrase=key_passphrase, repo=repo, branch=branch, name=name, app_port=app_port,
        domain=domain, skip_provision=skip_provision, yes=yes, verbose=verbose,
    )
    _handle_errors(lambda: _run_deploy(options))


@app.command("check-server")
def check_server(
    host: HostOpt = None,
    user: UserOpt = "root",
    port: PortOpt = 22,
    password: PasswordOpt = None,
    key_file: KeyFileOpt = None,
    key_passphrase: PassphraseOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Serverga ulanib, holatini ko'rsatish (hech narsa o'zgartirmaydi)."""
    ui.print_banner()

    def run() -> None:
        o = DeployOptions(
            host=host, user=user, port=port, password=password, key_file=key_file,
            key_passphrase=key_passphrase, verbose=verbose,
        )
        _ask_server(o, config.load_last() if not host else None)
        with _connect(o) as ssh:
            with ui.step("Server ma'lumotlarini yig'ish", verbose):
                facts = Provisioner(ssh).gather_facts()

        def badge(value: Optional[str]) -> str:
            return f"[green]{value}[/]" if value else "[red]o'rnatilmagan[/]"

        ui.console.print()
        ui.console.print(
            ui.summary_table(
                f"🖥  {o.host}",
                [
                    ("Hostname", facts.hostname),
                    ("OS", facts.os_name),
                    ("Kernel", facts.kernel),
                    ("Uptime", facts.uptime),
                    ("CPU yadrolari", facts.cpu_cores),
                    ("RAM", facts.memory),
                    ("Disk (/)", facts.disk),
                    (
                        "Huquqlar",
                        "[green]root[/]" if facts.is_root
                        else ("[green]sudo mavjud[/]" if facts.sudo_ok else "[red]sudo yo'q[/]"),
                    ),
                ],
            )
        )
        ui.console.print(ui.summary_table("📦 Dasturlar", [(k, badge(v)) for k, v in facts.tools.items()]))
        missing = [k for k, v in facts.tools.items() if not v]
        if missing:
            ui.info(f"Yetishmayotganlar ({', '.join(missing)}) deploy paytida o'zi o'rnatiladi.")
        else:
            ui.success("Server deploy uchun tayyor.")

    _handle_errors(run)


if __name__ == "__main__":
    app()

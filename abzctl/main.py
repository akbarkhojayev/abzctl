"""abzctl CLI kirish nuqtasi.

Buyruqlar:
    abzctl deploy        — loyihani serverga deploy qilish (wizard yoki flaglar orqali)
    abzctl check-server  — serverga ulanish va holatini tekshirish
"""

import os
import sys
from typing import Annotated, Optional

import paramiko
import typer
from rich.prompt import Confirm, IntPrompt, Prompt

from abzctl import __version__, ui
from abzctl.core.caddy import CaddyManager, check_dns, resolve_host, validate_domain
from abzctl.core.deployer import Deployer, project_name_from_repo, validate_repo_url
from abzctl.core.provisioner import Provisioner
from abzctl.core.ssh_client import ServerCredentials, SSHClient
from abzctl.exceptions import AbzctlError, SSHAuthError

app = typer.Typer(
    name="abzctl",
    help="🚀 SSH orqali Docker Compose loyihalarini Caddy + avtomatik SSL bilan deploy qiluvchi vosita.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["--help"]},  # -h --host uchun band
)

# ---------------------------------------------------------------------------- umumiy parametrlar

HostOpt = Annotated[Optional[str], typer.Option("--host", "-h", help="Server IP manzili yoki host nomi.")]
UserOpt = Annotated[str, typer.Option("--user", "-u", help="SSH foydalanuvchi nomi.")]
PortOpt = Annotated[int, typer.Option("--port", "-p", min=1, max=65535, help="SSH port.")]
PasswordOpt = Annotated[
    Optional[str],
    typer.Option(
        "--password",
        envvar="ABZCTL_PASSWORD",
        help="Server (va sudo) paroli. Berilmasa va kalit bo'lmasa — yashirin so'raladi.",
        show_default=False,
    ),
]
KeyFileOpt = Annotated[
    Optional[str], typer.Option("--key-file", "-k", help="SSH private key yo'li (masalan ~/.ssh/id_ed25519).")
]
PassphraseOpt = Annotated[
    Optional[str],
    typer.Option("--key-passphrase", envvar="ABZCTL_KEY_PASSPHRASE", help="Shifrlangan kalit passphrase'i.", show_default=False),
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Masofaviy buyruqlar chiqishini to'liq ko'rsatish.")]


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"abzctl v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Versiyani ko'rsatish."),
    ] = None,
) -> None:
    """abzctl — avtomatlashtirilgan deployer."""


# ---------------------------------------------------------------------------- yordamchi funksiyalar


def _handle_errors(func):
    """Buyruqni bajaradi va abzctl xatolarini rich paneli ko'rinishida chiqaradi."""
    try:
        func()
    except AbzctlError as exc:
        ui.console.print()
        ui.error_panel(exc.title, exc.message, exc.hint)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        ui.console.print()
        ui.warn("Foydalanuvchi tomonidan to'xtatildi.")
        raise typer.Exit(code=130)


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _ask_credentials(
    host: Optional[str],
    user: str,
    port: int,
    password: Optional[str],
    key_file: Optional[str],
    key_passphrase: Optional[str],
    wizard: bool,
) -> ServerCredentials:
    """Yetishmayotgan ulanish rekvizitlarini interaktiv so'raydi."""
    if not host:
        if not _is_interactive():
            raise AbzctlError("--host parametri berilmagan.", hint="Masalan: abzctl deploy --host 203.0.113.10 ...")
        host = Prompt.ask("[bold]Server IP manzili[/]")
        if wizard:
            user = Prompt.ask("[bold]SSH foydalanuvchi[/]", default=user)
            port = IntPrompt.ask("[bold]SSH port[/]", default=port)

    if not key_file and not password:
        if not _is_interactive():
            raise AbzctlError(
                "Na --password, na --key-file berilgan.",
                hint="Parolni ABZCTL_PASSWORD muhit o'zgaruvchisi orqali ham berish mumkin.",
            )
        method = Prompt.ask("[bold]Autentifikatsiya usuli[/]", choices=["password", "key"], default="password")
        if method == "key":
            default_key = next(
                (p for p in ("~/.ssh/id_ed25519", "~/.ssh/id_rsa") if os.path.isfile(os.path.expanduser(p))),
                "~/.ssh/id_rsa",
            )
            key_file = Prompt.ask("[bold]SSH kalit yo'li[/]", default=default_key)
        else:
            password = Prompt.ask(f"[bold]{user}@{host} paroli[/]", password=True)

    return ServerCredentials(
        host=host.strip(),
        user=user,
        port=port,
        password=password or None,
        key_file=key_file,
        key_passphrase=key_passphrase,
    )


def _connect(creds: ServerCredentials, verbose: bool) -> SSHClient:
    """Serverga ulanadi; kalit shifrlangan bo'lsa passphrase'ni so'rab qayta urinadi."""
    ssh = SSHClient(creds)
    title = f"Serverga ulanish: {creds.user}@{creds.host}:{creds.port}"
    try:
        with ui.step(title, verbose):
            ssh.connect()
    except SSHAuthError as exc:
        encrypted_key = isinstance(exc.__cause__, paramiko.PasswordRequiredException)
        if not (encrypted_key and creds.key_passphrase is None and _is_interactive()):
            raise
        creds.key_passphrase = Prompt.ask("[bold]SSH kalit passphrase[/]", password=True)
        with ui.step(title, verbose):
            ssh.connect()
    return ssh


# ---------------------------------------------------------------------------- deploy


@app.command()
def deploy(
    host: HostOpt = None,
    user: UserOpt = "root",
    port: PortOpt = 22,
    password: PasswordOpt = None,
    key_file: KeyFileOpt = None,
    key_passphrase: PassphraseOpt = None,
    repo: Annotated[Optional[str], typer.Option("--repo", "-r", help="Git repozitoriy havolasi.")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", "-b", help="Git branch (default: repo'ning asosiy branchi).")] = None,
    name: Annotated[Optional[str], typer.Option("--name", help="Loyiha nomi (default: repo nomidan olinadi).")] = None,
    app_port: Annotated[int, typer.Option("--app-port", min=1, max=65535, help="Ilovaning serverdagi porti (compose'da publish qilingan).")] = 3000,
    domain: Annotated[Optional[str], typer.Option("--domain", "-d", help="Domen nomi (ixtiyoriy), masalan api.example.com.")] = None,
    skip_provision: Annotated[bool, typer.Option("--skip-provision", help="Docker/Caddy o'rnatish bosqichini o'tkazib yuborish.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Tasdiqlashni so'ramaslik.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Loyihani serverga deploy qilish: Docker → git → docker compose → Caddy SSL.

    [bold]host[/] yoki [bold]repo[/] berilmasa, interaktiv wizard rejimi ishga tushadi.
    """
    ui.print_banner()

    def run() -> None:
        nonlocal repo, app_port, domain
        wizard = not host or not repo
        if wizard and _is_interactive():
            ui.info("Interaktiv rejim: yetishmayotgan ma'lumotlarni kiriting.\n")

        creds = _ask_credentials(host, user, port, password, key_file, key_passphrase, wizard)

        if not repo:
            if not _is_interactive():
                raise AbzctlError("--repo parametri berilmagan.")
            repo = Prompt.ask("[bold]Git repozitoriy havolasi[/]")
            app_port = IntPrompt.ask("[bold]Ilovaning ichki porti[/]", default=app_port)
            domain = Prompt.ask("[bold]Domen[/] [dim](ixtiyoriy, Enter — o'tkazib yuborish)[/]", default="") or None

        # Kiritilgan ma'lumotlarni serverga ulanishdan oldin tekshiramiz.
        repo = repo.strip()
        validate_repo_url(repo)
        project = name or project_name_from_repo(repo)
        if domain:
            domain = validate_domain(domain)

        ui.console.print()
        ui.console.print(
            ui.summary_table(
                "Deploy rejasi",
                [
                    ("Server", f"{creds.user}@{creds.host}:{creds.port}"),
                    ("Autentifikatsiya", f"kalit ({creds.key_file})" if creds.key_file else "parol"),
                    ("Repozitoriy", repo + (f" [dim]({branch})[/]" if branch else "")),
                    ("Loyiha papkasi", f"/var/www/{project}"),
                    ("Ilova porti", str(app_port)),
                    ("Domen", f"https://{domain}" if domain else "[dim]— (faqat IP:port)[/]"),
                ],
            )
        )
        if not yes and _is_interactive() and not Confirm.ask("\nDavom etamizmi?", default=True):
            ui.warn("Bekor qilindi.")
            raise typer.Exit()
        ui.console.print()

        ssh = _connect(creds, verbose)
        with ssh:
            provisioner = Provisioner(ssh)

            # --- 1. Serverni tayyorlash
            with ui.step("OS va huquqlarni tekshirish", verbose) as s:
                os_info = provisioner.detect_os()
                provisioner.check_privileges()
                s.update(os_info.pretty_name)
            ui.info(f"OS: {os_info.pretty_name} · foydalanuvchi: {'root' if ssh.is_root else creds.user + ' (sudo)'}")

            if not skip_provision:
                with ui.step("curl va git paketlarini tekshirish", verbose) as s:
                    installed = provisioner.ensure_base_packages(on_line=s.on_line)
                if installed:
                    ui.info(f"O'rnatildi: {', '.join(installed)}")

                with ui.step("Docker Engine'ni tekshirish/o'rnatish", verbose) as s:
                    if provisioner.ensure_docker(on_line=s.on_line):
                        ui.info("Docker yangi o'rnatildi.")

                with ui.step("Docker Compose'ni tekshirish", verbose) as s:
                    provisioner.ensure_compose(on_line=s.on_line)

                if domain:
                    with ui.step("Caddy'ni tekshirish/o'rnatish", verbose) as s:
                        caddy_source = provisioner.ensure_caddy(on_line=s.on_line)
                    if caddy_source:
                        ui.info(f"Caddy yangi o'rnatildi ({caddy_source}).")

            # --- 2. Loyihani deploy qilish
            deployer = Deployer(ssh, repo, branch=branch, project_name=project)

            with ui.step(f"{deployer.project_dir} papkasini tayyorlash", verbose):
                deployer.prepare_directory()

            with ui.step("Kodni git orqali olish", verbose) as s:
                action = deployer.sync_code(on_line=s.on_line)
            commit = deployer.current_commit()
            ui.info(f"{'Klonlandi' if action == 'cloned' else 'Yangilandi (git pull)'}: [bold]{commit}[/]")

            with ui.step("Compose faylini qidirish", verbose):
                compose_file = deployer.find_compose_file()

            with ui.step(f"docker compose up -d --build ({compose_file})", verbose) as s:
                deployer.compose_up(compose_file, on_line=s.on_line)

            ps = deployer.compose_ps(compose_file)
            if ps:
                ui.console.print(f"[dim]{ps}[/]")

            with ui.step(f"Ilovani tekshirish: localhost:{app_port}", verbose):
                healthy = deployer.wait_for_app(app_port)
            if not healthy:
                ui.warn(
                    f"Ilova localhost:{app_port} da javob bermadi. Compose faylida port "
                    f"'{app_port}:<ichki_port>' ko'rinishida publish qilinganini tekshiring."
                )

            # --- 3. Domen va SSL
            if domain:
                with ui.step("Firewall (UFW) da 80/443 portlarini ochish", verbose) as s:
                    provisioner.open_firewall_ports(on_line=s.on_line)

                with ui.step(f"Caddy: {domain} → localhost:{app_port}", verbose):
                    caddy_action = CaddyManager(ssh).configure_site(domain, app_port)
                ui.info(
                    {"added": "Caddyfile'ga yangi blok qo'shildi.",
                     "updated": "Caddyfile'dagi blok yangilandi.",
                     "unchanged": "Caddyfile o'zgarishsiz qoldi."}[caddy_action]
                )

        # --- 4. Yakuniy natija
        ui.console.print()
        if domain:
            server_ip = resolve_host(creds.host)
            dns_ips = check_dns(domain)
            if dns_ips and server_ip in dns_ips:
                dns_note = f"[green]✔ DNS to'g'ri: {domain} → {server_ip}[/]"
            else:
                current = ", ".join(dns_ips) if dns_ips else "topilmadi"
                dns_note = (
                    f"[yellow]⚠ DNS hali sozlanmagan (hozirgi qiymat: {current}).[/]\n"
                    f"Domen provayderingizda [bold]A-rekord[/] yarating:\n"
                    f"    [bold]{domain}[/]  →  [bold]{server_ip}[/]\n"
                    "DNS tarqalgach, Caddy Let's Encrypt sertifikatini avtomatik oladi."
                )
            ui.notice_panel(
                "🌐 Domen va SSL",
                f"{dns_note}\n\n80 va 443 portlari serverda ochiq bo'lishi shart.",
                style="green" if dns_ips and server_ip in dns_ips else "yellow",
            )
            url = f"https://{domain}"
        else:
            url = f"http://{creds.host}:{app_port}"

        ui.notice_panel(
            "🚀 Deploy yakunlandi",
            f"Loyiha: [bold]{project}[/]\nCommit: {commit}\nManzil: [bold cyan]{url}[/]",
            style="green",
        )

    _handle_errors(run)


# ---------------------------------------------------------------------------- check-server


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
    """Serverga ulanish va uning holatini (OS, resurslar, Docker, Caddy) tekshirish."""
    ui.print_banner()

    def run() -> None:
        creds = _ask_credentials(host, user, port, password, key_file, key_passphrase, wizard=not host)
        ssh = _connect(creds, verbose)
        with ssh:
            with ui.step("Server ma'lumotlarini yig'ish", verbose):
                facts = Provisioner(ssh).gather_facts()

        def badge(value: Optional[str]) -> str:
            return f"[green]{value}[/]" if value else "[red]o'rnatilmagan[/]"

        ui.console.print()
        ui.console.print(
            ui.summary_table(
                f"🖥  {creds.host}",
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
            ui.info(f"Yetishmayotganlar ([bold]{', '.join(missing)}[/]) `abzctl deploy` paytida avtomatik o'rnatiladi.")
        else:
            ui.success("Server deploy uchun to'liq tayyor.")

    _handle_errors(run)


if __name__ == "__main__":
    app()

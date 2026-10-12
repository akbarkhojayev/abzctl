"""Paramiko asosidagi SSH mijoz.

Imkoniyatlari:
- parol yoki SSH kalit (shifrlangan kalit + passphrase) orqali ulanish;
- buyruqlarni jonli (stream) chiqish bilan bajarish va exit code'ni tekshirish;
- root bo'lmagan foydalanuvchilar uchun buyruqlarni avtomatik `sudo` bilan o'rash;
- matnli fayllarni SFTP orqali yuklab, kerakli joyga `sudo` bilan joylashtirish.
"""

import logging
import os
import shlex
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import paramiko

from abzctl.exceptions import RemoteCommandError, SSHAuthError, SSHConnectionError

LineCallback = Callable[[str], None]

# Paramiko ichki xatolarni traceback ko'rinishida chiqaradi — biz ularni o'zimiz chiroyli ko'rsatamiz.
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

# Paket menejerlari interaktiv savollar bermasligi uchun barcha buyruqlarga qo'shiladi.
_ENV_PREFIX = "export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 LANG=C.UTF-8; "


@dataclass
class CommandResult:
    """Masofaviy buyruq natijasi."""

    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """stdout va stderr birlashtirilgan holda (xato xabarlari uchun qulay)."""
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


@dataclass
class ServerCredentials:
    """Serverga ulanish rekvizitlari."""

    host: str
    user: str = "root"
    port: int = 22
    password: Optional[str] = None
    key_file: Optional[str] = None
    key_passphrase: Optional[str] = None


class SSHClient:
    """Bitta serverga ulanish va unda buyruqlar bajarish uchun mijoz.

    Kontekst menejer sifatida ishlatish tavsiya etiladi:

        with SSHClient(creds) as ssh:
            ssh.run("uname -a")
    """

    def __init__(self, creds: ServerCredentials, connect_timeout: int = 15):
        self.creds = creds
        self.connect_timeout = connect_timeout
        self._client: Optional[paramiko.SSHClient] = None
        self._is_root: Optional[bool] = None

    # ------------------------------------------------------------------ ulanish

    def connect(self) -> None:
        """Serverga ulanadi. Xatoliklarni tushunarli abzctl xatolariga aylantiradi."""
        if self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return
            self.close()
        c = self.creds
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        key_file = os.path.expanduser(c.key_file) if c.key_file else None
        if key_file and not os.path.isfile(key_file):
            client.close()
            raise SSHAuthError(
                f"SSH kalit fayli topilmadi: {key_file}",
                hint="--key-file yo'lini tekshiring (masalan: ~/.ssh/id_ed25519).",
            )

        try:
            client.connect(
                hostname=c.host,
                port=c.port,
                username=c.user,
                password=c.password,
                key_filename=key_file,
                passphrase=c.key_passphrase,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                # Kalit berilgan bo'lsa, faqat o'shani ishlatamiz; aks holda ssh-agent va ~/.ssh ham sinab ko'riladi.
                allow_agent=key_file is None,
                look_for_keys=key_file is None and c.password is None,
            )
        except paramiko.PasswordRequiredException as exc:
            raise SSHAuthError(
                "SSH kalit shifrlangan, lekin passphrase kiritilmagan yoki noto'g'ri.",
                hint="--key-passphrase parametrini bering yoki so'ralganda kiriting.",
            ) from exc
        except paramiko.AuthenticationException as exc:
            method = "SSH kalit" if key_file else "parol"
            raise SSHAuthError(
                f"{c.user}@{c.host}:{c.port} serveriga {method} orqali kirib bo'lmadi.",
                hint=(
                    "Foydalanuvchi nomi va parolni tekshiring. Kalit ishlatilsa, ochiq kalit "
                    "serverdagi ~/.ssh/authorized_keys faylida bo'lishi kerak."
                ),
            ) from exc
        except paramiko.SSHException as exc:
            if "not a valid" in str(exc).lower() or "private key" in str(exc).lower():
                raise SSHAuthError(
                    f"SSH kalit faylini o'qib bo'lmadi: {exc}",
                    hint="Kalit formati (RSA/Ed25519/ECDSA) va fayl to'g'riligini tekshiring.",
                ) from exc
            raise SSHConnectionError(
                f"SSH protokol xatosi: {exc}",
                hint=("Server fingerprint'ini tekshiring va avval "
                      f"ssh -p {c.port} {c.user}@{c.host} orqali ulaning; kalit known_hosts'da bo'lishi kerak."),
            ) from exc
        except paramiko.ssh_exception.NoValidConnectionsError as exc:
            raise SSHConnectionError(
                f"{c.host}:{c.port} manziliga ulanib bo'lmadi (ulanish rad etildi).",
                hint="Server yoqilganini, SSH xizmati ishlayotganini va port to'g'riligini tekshiring.",
            ) from exc
        except socket.timeout as exc:
            raise SSHConnectionError(
                f"{c.host}:{c.port} ga ulanish vaqti tugadi ({self.connect_timeout}s).",
                hint="IP manzil to'g'riligini va firewall SSH portini ochiq qoldirganini tekshiring.",
            ) from exc
        except socket.gaierror as exc:
            raise SSHConnectionError(
                f"'{c.host}' manzilini aniqlab bo'lmadi.",
                hint="IP yoki host nomini tekshiring.",
            ) from exc
        except OSError as exc:
            raise SSHConnectionError(f"Tarmoq xatosi: {exc}") from exc
        finally:
            # Failed authentication/protocol connections must not leak sockets.
            if client.get_transport() is None or not client.get_transport().is_authenticated():
                client.close()

        # Uzoq davom etadigan buyruqlar (docker build) paytida ulanish uzilib qolmasligi uchun.
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._is_root = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def is_root(self) -> bool:
        """Ulangan foydalanuvchi root ekanligini aniqlaydi (natija keshlanadi)."""
        if self._is_root is None:
            result = self._exec("id -u")
            self._is_root = result.stdout.strip() == "0"
        return self._is_root

    # ------------------------------------------------------------------ buyruqlar

    def run(
        self,
        command: str,
        sudo: bool = False,
        check: bool = True,
        on_line: Optional[LineCallback] = None,
        timeout: Optional[float] = None,
        hint: Optional[str] = None,
    ) -> CommandResult:
        """Masofaviy buyruqni bajaradi.

        Args:
            command: bash buyrug'i (pipe, && va h.k. ishlatish mumkin).
            sudo: root huquqi kerakmi. Foydalanuvchi root bo'lsa, sudo ishlatilmaydi.
            check: True bo'lsa, exit code != 0 holatida RemoteCommandError ko'tariladi.
            on_line: chiqishdagi har bir qator uchun chaqiriladigan funksiya (jonli progress).
            timeout: buyruq uchun maksimal vaqt (soniya). None — cheklovsiz.
            hint: xatolik yuz berganda foydalanuvchiga ko'rsatiladigan maslahat.
        """
        wrapped = f"bash -c {shlex.quote(_ENV_PREFIX + command)}"
        stdin_data: Optional[str] = None

        if sudo and not self.is_root:
            if self.creds.password:
                # -S: parolni stdin'dan o'qish, -k: keshni e'tiborsiz qoldirish (parol har doim kutiladi),
                # -p '': sudo so'rov matnini chiqarmaslik.
                wrapped = f"sudo -S -k -p '' {wrapped}"
                stdin_data = self.creds.password + "\n"
            else:
                # Kalit bilan kirilganda parolsiz sudo (NOPASSWD) kutiladi.
                wrapped = f"sudo -n {wrapped}"

        result = self._exec(wrapped, stdin_data=stdin_data, on_line=on_line, timeout=timeout)
        # Foydalanuvchiga sudo o'rami emas, asl buyruq ko'rsatiladi (parol ham oshkor bo'lmaydi).
        result.command = command

        if check and not result.ok:
            if sudo and "sudo:" in result.stderr and "password" in result.stderr.lower():
                hint = (
                    "sudo parol talab qilyapti. --password bering yoki foydalanuvchiga "
                    "NOPASSWD sudo huquqi bering."
                )
            raise RemoteCommandError(command, result.exit_code, result.output, hint=hint)
        return result

    def command_exists(self, name: str) -> bool:
        """Serverda dastur mavjudligini `command -v` orqali tekshiradi."""
        return self.run(f"command -v {shlex.quote(name)}", check=False).ok

    def _exec(
        self,
        command: str,
        stdin_data: Optional[str] = None,
        on_line: Optional[LineCallback] = None,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """Past darajadagi bajarish: kanal ochadi, chiqishni qatorma-qator o'qiydi."""
        if self._client is None:
            raise SSHConnectionError("SSH ulanish o'rnatilmagan.")
        transport = self._client.get_transport()
        if transport is None or not transport.is_active():
            raise SSHConnectionError(
                "SSH ulanish uzilib qoldi.",
                hint="Tarmoqni tekshirib, buyruqni qayta ishga tushiring.",
            )

        channel = transport.open_session()
        channel.exec_command(command)
        if stdin_data is not None:
            channel.sendall(stdin_data.encode())
        channel.shutdown_write()

        stdout_chunks: list = []
        stderr_chunks: list = []
        # Qatorlarga bo'lish uchun bufer (stdout va stderr bitta oqim sifatida ko'rsatiladi).
        line_buffer = ""
        deadline = time.monotonic() + timeout if timeout else None

        def feed(data: str) -> None:
            nonlocal line_buffer
            if on_line is None:
                return
            line_buffer += data.replace("\r", "\n")
            *lines, line_buffer = line_buffer.split("\n")
            for line in lines:
                on_line(line)

        while True:
            got_data = False
            if channel.recv_ready():
                chunk = channel.recv(32768).decode("utf-8", errors="replace")
                stdout_chunks.append(chunk)
                feed(chunk)
                got_data = True
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(32768).decode("utf-8", errors="replace")
                stderr_chunks.append(chunk)
                feed(chunk)
                got_data = True
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
            if deadline and time.monotonic() > deadline:
                channel.close()
                raise RemoteCommandError(
                    command, -1, "".join(stdout_chunks), hint=f"Buyruq {timeout:.0f}s ichida tugamadi."
                )
            if not transport.is_active() and not channel.exit_status_ready():
                channel.close()
                raise SSHConnectionError("Buyruq bajarilayotganda SSH ulanish uzildi.")
            if not got_data:
                time.sleep(0.05)

        if on_line is not None and line_buffer:
            on_line(line_buffer)

        exit_code = channel.recv_exit_status()
        channel.close()
        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )

    # ------------------------------------------------------------------ fayllar

    def read_file(self, path: str, sudo: bool = False) -> Optional[str]:
        """Masofaviy faylni o'qiydi. Fayl mavjud bo'lmasa None qaytaradi."""
        quoted = shlex.quote(path)
        result = self.run(f"if [ -f {quoted} ]; then cat {quoted}; else exit 3; fi", sudo=sudo, check=False)
        if result.exit_code == 3:
            return None
        if not result.ok:
            raise RemoteCommandError(f"cat {path}", result.exit_code, result.output)
        return result.stdout

    def write_file(self, path: str, content: str, mode: str = "644", sudo: bool = False) -> None:
        """Matnli faylni yozadi: avval /tmp ga SFTP orqali yuklaydi, so'ng `install` bilan joylaydi.

        Bu usul root bo'lmagan foydalanuvchi uchun ham himoyalangan joylarga
        (masalan /etc/caddy) yozishga imkon beradi.
        """
        if self._client is None:
            raise SSHConnectionError("SSH ulanish o'rnatilmagan.")
        if not mode or len(mode) not in (3, 4) or any(char not in "01234567" for char in mode):
            raise ValueError("File mode must be an octal permission string")
        tmp_path = f"/tmp/abzctl-{uuid.uuid4().hex}"
        staged_path = f"{path}.abzctl-{uuid.uuid4().hex}"
        sftp = self._client.open_sftp()
        created = False
        try:
            # Exclusive creation prevents following an existing symlink. Restrict
            # permissions before writing any secret bytes.
            with sftp.file(tmp_path, "wx") as fh:
                created = True
                sftp.chmod(tmp_path, 0o600)
                fh.write(content)
            cleanup = shlex.quote(f"rm -f -- {shlex.quote(staged_path)}")
            self.run(
                f"trap {cleanup} EXIT; "
                f"install -m {mode} {shlex.quote(tmp_path)} {shlex.quote(staged_path)} "
                f"&& mv -fT -- {shlex.quote(staged_path)} {shlex.quote(path)}",
                sudo=sudo,
            )
        finally:
            try:
                if created:
                    sftp.remove(tmp_path)
            except (OSError, paramiko.SSHException):
                pass
            finally:
                sftp.close()

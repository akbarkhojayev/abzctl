"""abzctl uchun maxsus xatolik turlari.

Har bir xatolik foydalanuvchiga ko'rsatiladigan sarlavha (`title`) va
muammoni hal qilish bo'yicha maslahat (`hint`) saqlaydi. `main.py` ularni
ushlab, `rich` paneli ko'rinishida chiroyli chiqaradi.
"""

from typing import Optional


class AbzctlError(Exception):
    """Barcha abzctl xatoliklarining asosiy klassi."""

    title = "Xatolik"

    def __init__(self, message: str, hint: Optional[str] = None, title: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if title:
            self.title = title


class SSHConnectionError(AbzctlError):
    """Serverga ulanib bo'lmadi (tarmoq, port, timeout)."""

    title = "SSH ulanish xatosi"


class SSHAuthError(AbzctlError):
    """Parol yoki SSH kalit noto'g'ri."""

    title = "Autentifikatsiya xatosi"


class RemoteCommandError(AbzctlError):
    """Masofaviy buyruq noldan farqli exit code bilan tugadi."""

    title = "Masofaviy buyruq xatosi"

    def __init__(
        self,
        command: str,
        exit_code: int,
        output: str = "",
        hint: Optional[str] = None,
    ):
        # Chiqishning faqat oxirgi qatorlarini ko'rsatamiz — uzun loglar panelni to'ldirmasin.
        tail = "\n".join(output.strip().splitlines()[-15:])
        message = f"Buyruq: {command}\nExit code: {exit_code}"
        if tail:
            message += f"\n\nOxirgi chiqish:\n{tail}"
        super().__init__(message, hint=hint)
        self.command = command
        self.exit_code = exit_code
        self.output = output


class ProvisionError(AbzctlError):
    """Serverni sozlash (paket o'rnatish) bosqichidagi xatolik."""

    title = "Serverni sozlash xatosi"


class DeployError(AbzctlError):
    """Loyihani klonlash yoki ishga tushirishdagi xatolik."""

    title = "Deploy xatosi"


class CaddyError(AbzctlError):
    """Caddyfile yoki Caddy xizmati bilan bog'liq xatolik."""

    title = "Caddy xatosi"

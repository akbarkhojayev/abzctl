"""Terminal interfeysi: banner, bosqich spinnerlari, panellar va jadvallar.

Barcha chiqish yagona `console` obyekti orqali o'tadi, shuning uchun
spinner faol bo'lgan paytda ham loglar to'g'ri ko'rinadi.
"""

import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from abzctl import __version__

console = Console()

BANNER = r"""
         _              _   _
   __ _ | |__  ____ ___| |_| |
  / _` || '_ \|_  // __| __| |
 | (_| || |_) |/ /| (__| |_| |
  \__,_||_.__//___|\___|\__|_|
"""


def print_banner() -> None:
    """abzctl ASCII bannerini chiqaradi."""
    text = Text(BANNER, style="bold cyan")
    text.append(f"\n  v{__version__} · SSH → Docker Compose → Caddy (auto SSL)\n", style="dim")
    console.print(text)


def info(message: str) -> None:
    console.print(f"[cyan]ℹ[/] {message}")


def success(message: str) -> None:
    console.print(f"[bold green]✔[/] {message}")


def warn(message: str) -> None:
    console.print(f"[bold yellow]⚠[/] {message}")


def error_panel(title: str, message: str, hint: Optional[str] = None) -> None:
    """Xatolikni qizil panel ichida, maslahat bilan birga ko'rsatadi."""
    body = Text(message)
    if hint:
        body.append("\n\n💡 ", style="bold yellow")
        body.append(hint, style="yellow")
    console.print(Panel(body, title=f"[bold red]✖ {title}[/]", border_style="red", padding=(1, 2)))


def notice_panel(title: str, message: str, style: str = "yellow") -> None:
    """Muhim ogohlantirish yoki ma'lumot paneli."""
    console.print(Panel(message, title=f"[bold]{title}[/]", border_style=style, padding=(1, 2)))


def summary_table(title: str, rows: list) -> Table:
    """Ikki ustunli (kalit / qiymat) jadval yaratadi."""
    table = Table(title=title, title_style="bold cyan", show_header=False, border_style="cyan")
    table.add_column("Parametr", style="bold")
    table.add_column("Qiymat")
    for key, value in rows:
        table.add_row(key, value)
    return table


class Step:
    """Bitta bosqich uchun spinner holati.

    `on_line` metodi masofaviy buyruq chiqishidagi har bir qatorni qabul qiladi:
    - verbose rejimida qator spinner ustida xira rangda chop etiladi;
    - aks holda spinner matni oxirgi qator bilan yangilanib boradi (jonli progress).
    """

    def __init__(self, title: str, status, verbose: bool):
        self.title = title
        self._status = status
        self._verbose = verbose

    def update(self, message: str) -> None:
        self._status.update(f"[bold cyan]{escape(self.title)}[/] [dim]— {escape(message)}[/]")

    def on_line(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        if self._verbose:
            console.print(f"    [dim]│ {escape(line)}[/]")
        # Juda uzun qatorlarni spinner uchun qisqartiramiz.
        short = line if len(line) <= 80 else line[:77] + "..."
        self.update(short)


@contextmanager
def step(title: str, verbose: bool = False) -> Iterator[Step]:
    """Bosqichni spinner bilan bajaradi va natijasini (✔ / ✖) hamda vaqtini chiqaradi."""
    started = time.monotonic()
    with console.status(f"[bold cyan]{escape(title)}[/]", spinner="dots") as status:
        current = Step(title, status, verbose)
        try:
            yield current
        except Exception:
            elapsed = time.monotonic() - started
            console.print(f"[bold red]✖[/] {escape(title)} [dim]({elapsed:.1f}s)[/]")
            raise
    elapsed = time.monotonic() - started
    console.print(f"[bold green]✔[/] {escape(title)} [dim]({elapsed:.1f}s)[/]")


LineCallback = Callable[[str], None]

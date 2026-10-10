#!/usr/bin/env bash
# abzctl o'rnatuvchisi (Linux va macOS).
#
# Ishlatish:  bash install.sh
#
# Nima qiladi:
#   1. Python 3.9+ borligini tekshiradi (Debian/Ubuntu'da yetishmasa o'zi o'rnatadi)
#   2. ~/.abzctl/venv ichiga abzctl va uning kutubxonalarini o'rnatadi
#   3. `abzctl` buyrug'ini ~/.local/bin ga qo'yadi va PATH'ga qo'shadi
#
# Qayta ishga tushirilsa — abzctl yangilanadi.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${ABZCTL_HOME:-$HOME/.abzctl}"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"

green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
cyan()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

cyan "==> abzctl o'rnatilmoqda"

# --- 1. Python ---------------------------------------------------------------
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1 &&
           "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        cyan "==> Python topilmadi, o'rnatilmoqda (sudo paroli so'ralishi mumkin)"
        sudo apt-get update -q
        sudo apt-get install -y -q python3 python3-venv
        PY="$(find_python)"
    else
        red "Python 3.9+ topilmadi."
        red "macOS: brew install python   |   boshqa tizimlar: https://www.python.org/downloads/"
        exit 1
    fi
fi
echo "    Python: $("$PY" --version)"

# --- 2. Virtual muhit va abzctl ---------------------------------------------
if ! "$PY" -m venv --help >/dev/null 2>&1 || ! "$PY" -c 'import ensurepip' 2>/dev/null; then
    if command -v apt-get >/dev/null 2>&1; then
        cyan "==> python3-venv o'rnatilmoqda"
        PY_VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        sudo apt-get install -y -q "python${PY_VER}-venv" || sudo apt-get install -y -q python3-venv
    fi
fi

cyan "==> Kutubxonalar o'rnatilmoqda ($INSTALL_DIR)"
mkdir -p "$INSTALL_DIR"
"$PY" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install -q --upgrade pip
"$VENV_DIR/bin/python" -m pip install -q --upgrade "$SRC_DIR"

# --- 3. Global buyruq --------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/abzctl" "$BIN_DIR/abzctl"

PATH_ADDED=0
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        LINE='export PATH="$HOME/.local/bin:$PATH"'
        for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
            if [ -f "$rc" ] && ! grep -qF "$LINE" "$rc"; then
                printf '\n# abzctl\n%s\n' "$LINE" >> "$rc"
            fi
        done
        PATH_ADDED=1
        ;;
esac

echo
green "✔ abzctl o'rnatildi ($("$VENV_DIR/bin/abzctl" --version))"
echo
if [ "$PATH_ADDED" = 1 ]; then
    echo "  Terminalni yopib qayta oching (yoki: source ~/.bashrc), so'ng yozing:"
else
    echo "  Deploy qilish uchun yozing:"
fi
echo
green "      abzctl"
echo

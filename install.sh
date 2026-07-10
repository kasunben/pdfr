#!/usr/bin/env bash
#
# pdfr installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/kasunben/pdfr/main/install.sh | bash
#
# Installs:
#   ~/.pdfr/pdfr.py      - the tool
#   ~/.pdfr/venv/          - isolated Python env with dependencies
#   ~/.local/bin/pdfr       - launcher on your PATH
#
set -euo pipefail

# ---- Configure this before hosting ----------------------------------------
REPO_RAW_BASE="https://raw.githubusercontent.com/kasunben/pdfr/main"
# -----------------------------------------------------------------------------

INSTALL_DIR="$HOME/.pdfr"
BIN_DIR="$HOME/.local/bin"

echo "Installing pdfr..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found on PATH." >&2
  exit 1
fi

# QR code / barcode detection needs the zbar shared library. It's optional —
# pdfr still works for all text-based PII types without it, just skips
# QR/barcode scanning with a warning.
if ! python3 -c "import ctypes; ctypes.util.find_library('zbar')" >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "  -> installing libzbar0 (QR/barcode support) via apt"
    sudo apt-get install -y libzbar0 >/dev/null 2>&1 || \
      echo "     (couldn't install libzbar0 automatically — QR/barcode scanning will be skipped)"
  elif command -v brew >/dev/null 2>&1; then
    echo "  -> installing zbar (QR/barcode support) via brew"
    brew install zbar >/dev/null 2>&1 || \
      echo "     (couldn't install zbar automatically — QR/barcode scanning will be skipped)"
  else
    echo "  -> zbar not found and no supported package manager detected;"
    echo "     QR/barcode scanning will be skipped until it's installed manually."
  fi
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

echo "  -> downloading pdfr.py"
curl -fsSL "$REPO_RAW_BASE/pdfr.py" -o "$INSTALL_DIR/pdfr.py"

echo "  -> creating virtual environment"
python3 -m venv "$INSTALL_DIR/venv"

echo "  -> installing dependencies"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet pymupdf pillow pyzbar phonenumbers

echo "  -> installing launcher at $BIN_DIR/pdfr"
cat > "$BIN_DIR/pdfr" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/pdfr.py" "\$@"
EOF
chmod +x "$BIN_DIR/pdfr"

echo ""
echo "pdfr installed."

case ":$PATH:" in
  *":$BIN_DIR:"*)
    echo "Try it now: pdfr --version"
    ;;
  *)
    echo "NOTE: $BIN_DIR is not on your PATH yet."
    echo "Add this line to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo ""
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "Then restart your shell, or run it directly:"
    echo "  $BIN_DIR/pdfr --version"
    ;;
esac

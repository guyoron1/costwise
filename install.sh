#!/usr/bin/env bash
set -e

echo "Costwise — installing everything"
echo ""

# Install costwise with all Python extras
echo "Installing costwise + Python extras (Graphify, Headroom)..."
pip install -e ".[all]" -q
echo "  ✓ Done"

# Install RTK
if command -v rtk &>/dev/null; then
    echo "  ✓ RTK — already installed"
else
    echo "Installing RTK..."
    if command -v brew &>/dev/null; then
        brew install rtk
        echo "  ✓ RTK installed"
    else
        echo "  ✗ RTK — brew not found, install manually: https://github.com/rtk-ai/rtk#installation"
    fi
fi

# Install Ponytail
if npm list -g @dietrichgebert/ponytail &>/dev/null 2>&1; then
    echo "  ✓ Ponytail — already installed"
else
    echo "Installing Ponytail..."
    if command -v npm &>/dev/null; then
        npm install -g @dietrichgebert/ponytail
        echo "  ✓ Ponytail installed"
    else
        echo "  ✗ Ponytail — npm not found, install manually: https://github.com/DietrichGebert/ponytail#installation"
    fi
fi

echo ""
echo "Done. Run 'costwise doctor' to verify."

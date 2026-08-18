#!/usr/bin/env sh
set -eu

ELECTROHIRE_PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v electrohire-zoo-code >/dev/null 2>&1; then
  exec electrohire-zoo-code --project "$ELECTROHIRE_PROJECT_ROOT" "$@"
fi
ELECTROHIRE_ZOO_TOOL="$ELECTROHIRE_PROJECT_ROOT/../zoo-code-configurator/configure-zoo-code.sh"
if [ -f "$ELECTROHIRE_ZOO_TOOL" ]; then
  exec sh "$ELECTROHIRE_ZOO_TOOL" --project "$ELECTROHIRE_PROJECT_ROOT" "$@"
fi
ELECTROHIRE_ZOO_TOOL="$ELECTROHIRE_PROJECT_ROOT/../../zoo-code-configurator/configure-zoo-code.sh"
if [ -f "$ELECTROHIRE_ZOO_TOOL" ]; then
  exec sh "$ELECTROHIRE_ZOO_TOOL" --project "$ELECTROHIRE_PROJECT_ROOT" "$@"
fi
echo "ElectroHire Zoo Code Configurator was not found." >&2
echo "Clone https://github.com/ElectroHire/zoo-code-configurator beside this repository or install it with pip." >&2
exit 1

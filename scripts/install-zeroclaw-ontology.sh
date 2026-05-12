#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${ZEROCLAW_RUNTIME_DIR:-$ROOT_DIR/runtime}"
SKILLS_SRC="$ROOT_DIR/zeroclaw/skills"
BIN_SRC="$ROOT_DIR/zeroclaw/bin/ontology"
SKILLS_DEST="$RUNTIME_DIR/workspace/.agents/skills"
BIN_DEST="$RUNTIME_DIR/config/bin"

mkdir -p "$SKILLS_DEST" "$BIN_DEST"

rm -rf "$SKILLS_DEST/ontology-ledger" "$SKILLS_DEST/ontology-chain"
cp -R "$SKILLS_SRC/ontology-ledger" "$SKILLS_DEST/ontology-ledger"
cp -R "$SKILLS_SRC/ontology-chain" "$SKILLS_DEST/ontology-chain"

cp "$BIN_SRC" "$BIN_DEST/ontology"
chmod +x "$BIN_DEST/ontology"

cat <<EOF
Installed OntologyAgent ZeroClaw capabilities:
  skills: $SKILLS_DEST/ontology-ledger
          $SKILLS_DEST/ontology-chain
  cli:    $BIN_DEST/ontology

Mount $BIN_DEST/ontology into the ZeroClaw container as /usr/local/bin/ontology
and ensure runtime/config/config.toml allows the ontology command.
EOF

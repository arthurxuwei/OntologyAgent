#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${ZEROCLAW_RUNTIME_DIR:-$ROOT_DIR/runtime}"
SKILLS_SRC="$ROOT_DIR/zeroclaw/skills"
BIN_SRC="$ROOT_DIR/zeroclaw/bin/ontology"
WORKSPACE_SKILLS_DEST="$RUNTIME_DIR/workspace/skills"
AGENTS_SKILLS_DEST="$RUNTIME_DIR/workspace/.agents/skills"
BIN_DEST="$RUNTIME_DIR/config/bin"

mkdir -p "$WORKSPACE_SKILLS_DEST" "$AGENTS_SKILLS_DEST" "$BIN_DEST"

for skill_dir in "$SKILLS_SRC"/*; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  rm -rf "$WORKSPACE_SKILLS_DEST/$skill_name" "$AGENTS_SKILLS_DEST/$skill_name"
  cp -R "$skill_dir" "$WORKSPACE_SKILLS_DEST/$skill_name"
  cp -R "$skill_dir" "$AGENTS_SKILLS_DEST/$skill_name"
done

cp "$BIN_SRC" "$BIN_DEST/ontology"
chmod +x "$BIN_DEST/ontology"

cat <<EOF
Installed OntologyAgent ZeroClaw capabilities:
  skills: $WORKSPACE_SKILLS_DEST/ontology-*
          $AGENTS_SKILLS_DEST/ontology-*
  cli:    $BIN_DEST/ontology

Mount $BIN_DEST/ontology into the ZeroClaw container as /usr/local/bin/ontology
and ensure runtime/config/config.toml allows the ontology command.
EOF

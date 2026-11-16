#!/usr/bin/env bash
# Install the MetaLoop skill for a coding agent other than Claude Code (Claude Code installs the plugin; see README).
# Links skills/metaloop from this clone, so a `git pull` here updates it.
#
#   ./install.sh codex | agy | gemini | grok | opencode | cursor | all     for you, in the agent's user skills folder
#   ./install.sh --project <repo>                                          in one repository's .agents/skills (read by
#                                                                          Codex, Antigravity, Gemini CLI, OpenCode, Cursor)
#   ./install.sh --copy ...                                                copy instead of linking
#   ./install.sh --remove <agent> | --remove --project <repo>              take it out again
#
# User folders: Codex and Grok Build read ~/.agents/skills, Antigravity and Gemini CLI ~/.gemini/skills, Grok Build also
# ~/.grok/skills (and Claude Code's plugins). OpenCode and Cursor read .agents/skills in a project: use --project.
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/skills/metaloop"
mode=link
while [ $# -gt 0 ]; do
    case "$1" in
        --copy) mode=copy; shift ;;
        --remove) mode=remove; shift ;;
        *) break ;;
    esac
done
place() {  # <skills folder>
    local d="$1" dst="$1/metaloop"
    case "$mode" in
        remove) rm -rf "$dst"; echo "removed $dst" ;;
        copy) mkdir -p "$d"; rm -rf "$dst"; cp -R "$SRC" "$dst"; echo "copied the skill to $dst" ;;
        link) mkdir -p "$d"
              if [ -e "$dst" ] && [ ! -L "$dst" ]; then echo "$dst exists and is not a link: remove it or use --copy"; exit 1; fi
              ln -sfn "$SRC" "$dst"; echo "linked $dst -> $SRC" ;;
    esac
}
case "${1:-}" in
    "") sed -n '2,14p' "$0"; exit 2 ;;
    --project) [ -n "${2:-}" ] && [ -d "$2" ] || { echo "--project needs a repository folder"; exit 2; }
               place "$(cd "$2" && pwd)/.agents/skills"
               echo "Commit .agents/skills/metaloop as a copy (--copy) to share it with the team; a link only works on this machine." ;;
    codex) place "$HOME/.agents/skills" ;;
    agy|gemini) place "$HOME/.gemini/skills" ;;
    grok) place "$HOME/.grok/skills" ;;
    opencode|cursor) echo "$1 reads skills from a project's .agents/skills: ./install.sh --project <repo>"; exit 2 ;;
    all) place "$HOME/.agents/skills"; place "$HOME/.gemini/skills"; place "$HOME/.grok/skills" ;;
    claude) echo "Claude Code: claude plugin install metaloop --marketplace jomitiron/MetaLoop" ;;
    *) echo "unknown agent '$1' (codex, agy, gemini, grok, opencode, cursor, all, --project <repo>)"; exit 2 ;;
esac
echo "Start a new session of the agent: it picks MetaLoop up when the work fits. The kit is the kit folder beside SKILL.md."

#!/usr/bin/env bash
# Applies this repository's GitHub configuration ("settings as code") with the gh CLI and the REST API.
# Idempotent: safe to re-run. Every step reports OK or SKIP with GitHub's own message, because several
# features are plan-dependent (on the free plan, rulesets, code scanning, secret scanning and private
# vulnerability reporting only exist for PUBLIC repositories). Re-run it after changing visibility.
#
#   scripts/github/apply-settings.sh omsingh02/REPO
#
# Requires: gh >= 2.40 logged in with the `repo` and `workflow` scopes.
set -uo pipefail

SLUG="${1:?usage: apply-settings.sh omsingh02/REPO}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="repos/$SLUG"

step() {  # step "description" command...
  local name="$1"; shift
  local out
  if out=$("$@" 2>&1); then
    printf '  OK    %s\n' "$name"
  else
    printf '  SKIP  %s -> %s\n' "$name" "$(printf '%s' "$out" | tr '\n' ' ' | grep -oE '"message":"[^"]*"' | head -1)"
  fi
}

echo "== repository =="
step "features: issues + discussions on; wiki + projects off" \
  gh api -X PATCH "$R" -F has_issues=true -F has_wiki=false -F has_projects=false -F has_discussions=true
step "merge policy: squash/rebase only, delete merged branches, auto-merge, update-branch" \
  gh api -X PATCH "$R" -F allow_squash_merge=true -F allow_rebase_merge=true -F allow_merge_commit=false \
    -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY \
    -F delete_branch_on_merge=true -F allow_auto_merge=true -F allow_update_branch=true -F web_commit_signoff_required=false

echo "== security =="
step "Dependabot alerts"                    gh api -X PUT "$R/vulnerability-alerts"
step "Dependabot security updates"          gh api -X PUT "$R/automated-security-fixes"
step "private vulnerability reporting"      gh api -X PUT "$R/private-vulnerability-reporting"
step "secret scanning + push protection" \
  gh api -X PATCH "$R" -f 'security_and_analysis[secret_scanning][status]=enabled' \
                       -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
# CodeQL runs from .github/workflows/codeql-advanced.yml. Do NOT also enable "default setup": GitHub rejects SARIF
# uploads from a workflow while default setup is on. (The workflow skips itself while the repository is private.)

echo "== Actions =="
step "default GITHUB_TOKEN is read-only; Actions cannot approve PRs" \
  gh api -X PUT "$R/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
step "only GitHub-owned actions may run" \
  gh api -X PUT "$R/actions/permissions" -F enabled=true -f allowed_actions=selected
step "allow-list: GitHub-owned only" \
  gh api -X PUT "$R/actions/permissions/selected-actions" -F github_owned_allowed=true -F verified_allowed=false
step "approval needed for workflow runs from outside contributors" \
  gh api -X PUT "$R/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors

echo "== rulesets =="
apply_ruleset() {  # apply_ruleset file  (create, or update if one with the same name exists)
  local file="$1" name id body
  body="$(cat "$file")"
  name="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
  id="$(gh api "$R/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)"
  if [ -n "$id" ]; then
    step "ruleset $name (update)" bash -c "printf '%s' '$(printf '%s' "$body" | sed "s/'/'\\\\''/g")' | gh api -X PUT $R/rulesets/$id --input -"
  else
    step "ruleset $name (create)" bash -c "printf '%s' '$(printf '%s' "$body" | sed "s/'/'\\\\''/g")' | gh api -X POST $R/rulesets --input -"
  fi
}
apply_ruleset "$HERE/rulesets/main.json"
apply_ruleset "$HERE/rulesets/release-tags.json"

echo "== labels =="
label() { gh label create "$1" --repo "$SLUG" --color "$2" --description "$3" --force >/dev/null 2>&1 \
  && printf '  OK    %s\n' "$1" || printf '  SKIP  %s\n' "$1"; }
# .github/labels.yml is the single source of truth (Dependabot, the issue forms and release.yml use these names).
while IFS=$'\t' read -r name color description; do
  label "$name" "$color" "$description"
done < <(python3 - "$HERE/../../.github/labels.yml" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
for block in re.split(r"(?m)^- name: ", text)[1:]:
    name, _, rest = block.partition("\n")
    color = re.search(r"(?m)^\s+color: (\S+)", rest).group(1)
    description = re.search(r"(?m)^\s+description: (.*)$", rest).group(1)
    print("\t".join(value.strip().strip("'\"") for value in (name, color, description)))
PY
)
echo "done."

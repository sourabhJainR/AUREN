# Publish a new AUREN project to GitHub

AUREN can initialize an independent local project and then publish it as a new GitHub repository using the laptop's authenticated GitHub CLI.

## 1. Initialize the local project

```powershell
python auren_cli.py handoff new-project --path C:\Work\NewProject --name NewProject
```

Review the generated README, ignore rules and project profile. Add and commit application files yourself before publishing.

## 2. Authenticate GitHub CLI

Install GitHub CLI and authenticate once:

```powershell
gh auth login
gh auth status
```

AUREN does not accept or store a GitHub token. Authentication remains in the user's local GitHub CLI credential store.

## 3. Create the remote repository and push

Run from the AUREN source checkout or installed package:

```powershell
python -m portable.github_project_publish --path C:\Work\NewProject --repository sourabhJainR/NewProject --visibility private --description "New project managed with AUREN"
```

Private visibility is the default. Choose public or internal only when intended and supported by the target GitHub account.

The command requires:
- An initialized Git repository with a named branch and at least one commit.
- A clean worktree, including no untracked files. AUREN will not stage, commit or discard your work.
- No existing `origin` remote. AUREN will not replace an existing remote.
- A successful local `gh auth status`.

After repository creation, AUREN verifies the repository identity, origin URL and pushed branch SHA against the local HEAD. If verification fails after GitHub creates the repository, the command reports a failure and leaves the repository available for manual inspection; it does not delete remote resources.

## 4. Start AUREN on the published project

```powershell
python auren_cli.py up --project-root C:\Work\NewProject
```

The local project publisher is a delivery helper, not a coding executor. It does not approve code, run repository changes, merge pull requests or bypass Guard. Subsequent changes must use the AUREN task lifecycle and its independent approval and verification gates.

## Current boundary

This command completes local-to-GitHub repository creation and initial push. It does not deploy a hosted MCP endpoint, configure ChatGPT workspace OAuth, or make ChatGPT Web capable of accessing the laptop directly. Those remain separate deployment and live-client acceptance requirements.

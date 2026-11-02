---
name: package-lab
description: Package this Ignite lab for a Skillable VM with a fresh Git baseline, then explain Resources-tab upload, Edge cookie cleanup, Azure sign-out, and VM image commit. Use when asked to package the lab, rebuild its ZIP, prepare a VM handoff, or prepare the lab image.
---

# Package the lab

## Build the ZIP

1. Work from the repository root. Read `git status --short` and inventory current
   files with `git ls-files --cached --others --exclude-standard -z`. Include
   current staged, unstaged, and eligible untracked content, not just `HEAD`.
   Preserve the source repository's index, commits, branches, and remotes.
2. Review candidate filenames before copying. Exclude the source `.git`, `.env`
   and other environment/credential files, `.azure`, `.venv`, caches, logs,
   generated root `evidence/`, test reports, coverage output, ZIPs, and `.skill`
   archives. Exclude private keys and certificates. Keep authored
   `src/agents/evidence/` and `data/evidence/`. Reject symlinks or junctions that
   could pull in files outside the checkout. Do not copy unknown credentials.
3. Copy the selected current files into a new temporary staging directory.
   Never initialize Git or run `git add --all` in the source checkout for packaging.
   Do not add a packaging script, dependency, or uv command: execute this workflow
   with the available file, Git, and archive tools.
4. Inside the staging directory only, create a fresh repository:

   ```text
   git init --initial-branch main --template=""
   git config core.autocrlf false
   git config core.filemode false
   git add --all
   git commit -m "Initial lab baseline"
   ```

   Use a neutral author and committer such as `Lab baseline <lab@example.invalid>`,
   disable signing and external Git hooks for this temporary commit, and include
   trailers required by the active session. Do not copy the original Git history,
   remote URLs, credentials, hooks, global configuration, or author identity.
   Commit every staged lab file; fail if an expected file was silently ignored.
5. Create `ignite26-PREL35-lab.zip` at the source repository root from the staged
   directory. Put the lab files and the newly created `.git/` directly at the
   archive root, without an extra containing folder. Include hidden files.
   Do not put an earlier ZIP inside the new ZIP.
6. Extract into a second temporary directory and verify archive integrity, file
   contents against the selected current files, required lab files, and exclusions.
   Check that `git status --porcelain` is empty, the branch is `main`,
   `git rev-list --count HEAD` is `1`, and `git remote -v` is empty.
   Check that `azure.yaml` has no predeploy hook and contains the current
   postprovision platform launchers. Do not run deployment or billable searches.
7. Remove only the temporary staging and verification directories created for
   this run. Report the archive path, size, SHA-256, and clean baseline.
   Do not claim that the VM was uploaded or committed.

## Hand off through the Resources tab

Include these instructions in the handoff; do not perform browser logout or VM
image operations on the packaging host.

1. Upload `ignite26-PREL35-lab.zip` through the running lab's **Resources** tab
   and transfer/download it onto the Windows VM.
2. Extract into a fresh, empty
   `C:\Users\LabUser\Desktop\ignite26-PREL35` directory. Do not merge over an old
   checkout with stale `.env`, `.azure`, or `.git` content.
   Confirm that the archive's hidden `.git` directory was extracted.
3. Run `git status --short` and `git log -1 --oneline` in that directory.
   Expect a clean tree and the initial lab baseline commit. Do not initialize
   Git again or commit personal credentials on the VM.
4. Run `uv sync` if the VM needs its dependencies restored. Keep initial
   learner setup separate from Web IQ and hosted-agent preparation.

## Clean the VM before committing its image

Perform these steps on the **VM**, after any smoke checks and before saving its
image. Explain that committing the VM means Skillable's image commit/save action,
not another Git commit.

1. Sign out of the Azure and Foundry portals and any other Microsoft browser
   sessions used for testing. Sign out of any personal Edge profile used during
   preparation so browser sync cannot restore the account.
2. Open **`edge://settings/content/all`** in Edge and clear **all cookies and
   stored site data**, not just the current site's data. Close every Edge window.
   Do not disconnect a managed Windows work account merely to clear browser state.
3. Sign out of Azure tooling in the VM terminal:

   ```powershell
   az logout
   az account clear
   azd auth logout
   ```

4. If setup was tested on the VM, remove the generated project `.env` and
   project `.azure` directory after reviewing them. Leave no Web IQ keys, access
   tokens, saved passwords, or test-account credentials in the image.
   Clear the clipboard and any terminal history containing pasted credentials.
5. Verify the browser and Azure tooling no longer retain the test sign-in,
   close terminals and applications, then use Skillable's **commit/save VM image**
   action. Do not commit the image until sign-out and cleanup are complete.

# Fresh Ken VPS installations

This workflow installs the current local source on a **new Ubuntu 24.04 VM**. It does not create or buy servers, clone an existing customer's machine, or update a configured Ken installation. Use a separate VM and Telegram bot for each customer.

## 1. Create a fresh VM

In Hetzner (or another VPS provider), select Ubuntu 24.04, an x86 machine with at least 4 GB RAM, and your operator SSH public key. 8 GB gives more room for browsers and local transcription. Choose the region for the customer and enable provider backups if wanted; check their additional cost at checkout.

Restrict inbound access to SSH from your operator IP or private network. Ken polls Telegram outbound and needs no public web port. The bootstrap leaves SSH/firewall configuration alone to avoid locking you out; configure that in your provider console. It creates a non-root `ken` user with no sudo rights. Ken can install user-local tools; system packages require the operator.

Verify the host-key fingerprint through the provider console, then connect once using `ssh root@NEW_IP`. The deployment commands require an already trusted SSH host key and disable SSH agent forwarding. An SSH alias can specify a custom port/key. Never use your existing production box as a test target.

## 2. Prepare the machine

From this repository on your Mac:

```bash
python3 deploy/vps.py prepare root@NEW_IP --timezone Europe/Lisbon
```

This uploads an explicit allowlist of source files, installs Linux prerequisites, creates `/home/ken`, sets the requested machine timezone, and enables the user service manager at boot. It records file hashes in `~ken/.ken/app/release-manifest.json`. It does not copy local credentials, memory, or SSH keys.

Preparation refuses configured Ken installations and known legacy paths, including `/opt/ken` and `/root/ken-assistant`. It can be retried after a failed preparation on a machine it previously initialized, before credentials are configured. This is a fresh-machine installer, not a general migration tool.

## 3. Onboard that machine's owner

Run this yourself in an interactive terminal (credential input is hidden):

```bash
python3 deploy/vps.py onboard root@NEW_IP
```

Create a new bot with Telegram's BotFather. Paste its token into the SSH onboarding terminal, then send the displayed one-time pairing code to that bot in a private chat. Only the account sending that code is linked.

Enter the owner's Anthropic API key in the terminal. The hosted flow uses API billing, including a small paid authentication check. Set an appropriate budget in the owner's provider account. Do not paste credentials into Ken's Telegram chat or a support conversation. Existing subscription onboarding remains available through the original self-hosted installer; this hosted workflow does not collect subscription tokens.

The service runs as `ken`, keeps customer data in `/home/ken/.ken`, and uses the VM timezone for schedules. The uploaded Ken source does not auto-update; Python package versions and the initial Claude binary version are recorded. Dependencies are freshly resolved on initial installation, so identical source bundles are not yet fully reproducible machine images. Claude's native updater is separate from Ken's source updater.

If onboarding fails before `.env` is written, rerun it. If `.env` exists, the wrapper refuses to overwrite it: inspect the failure and finish service setup manually as the `ken` user rather than deleting customer data. No existing install is silently reconfigured.

## 4. Verify before giving it to someone

```bash
python3 deploy/vps.py check root@NEW_IP
```

This checks service startup, lingering, owner configuration, private config permissions, memory/schedule files, timezone, and disk space. It does not claim that external APIs work merely because the process is running.

Then use Telegram to verify text, voice transcription, generated-file delivery, memory recall, and `/stop`. Set a brief a few minutes ahead and confirm it arrives at the intended local time. Reboot the **new** VM through the provider console, reconnect, rerun the check, and confirm memory recall and replies. Reboot testing cannot be replaced by a Docker test.

The existing scheduler still lacks missed-job catch-up/retries; a reboot during the scheduled minute can miss that run. This workflow does not change those scheduling semantics.

## 5. Back up and rehearse restoration

Download a backup onto your operator computer:

```bash
python3 deploy/vps.py backup root@NEW_IP /private/tmp/ken-pilot-backup.tar.gz
python3 deploy/restore.py /private/tmp/ken-pilot-backup.tar.gz /private/tmp/ken-restore-drill
```

Choose new output paths each time. The backup pauses the Ken service briefly and restarts it afterward. SSH encrypts transport and the local archive is mode 600, but **the archive itself is not encrypted**. Store retained copies on encrypted storage, outside the repository. It includes the Ken user's home, credentials, files, source, and Claude session data; caches and the Python virtualenv are excluded. Other background programs may still write files during backup. System packages, root-owned services, and data outside `/home/ken` are not included: provider backups/snapshots are needed for whole-machine recovery.

The restore helper requires Python 3.12+, extracts into a new private directory, rejects unsafe archive paths/links, and never starts a bot. Check expected memory, jobs, customer files, and `requirements-deployed.txt` there. Do not print credential files.

For disaster recovery, provision a replacement VM, stop the original bot before activating its identity on the replacement, restore the home data, recreate the Python venv using `requirements-deployed.txt`, and reinstall the user service. Verify any system tools and external account sessions separately. A successful extraction drill establishes file recovery; the reboot/API checks on a replacement establish operational recovery.

Backups are on demand here. Automated off-machine backup scheduling and external downtime alerts require choosing a destination/monitor and remain deployment follow-ups. A systemd restart policy alone does not detect all stuck agents or provider outages.

## Releases

The first pilot uses an uploaded source bundle. `ken update` explains this instead of pulling an unrelated Git branch. Do not rerun `prepare` over an existing customer. Until a release-update command is implemented, updates are operator maintenance: take a backup/provider snapshot, stage and test the next source/dependencies, stop Ken, switch the code, restart, verify, and retain the previous source/venv for rollback. Never replace customer memory or `.env` during an update.

## Local validation

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh ken deploy/bootstrap.sh deploy/preflight.sh deploy/check.sh deploy/backup.sh
```

These checks do not contact Telegram/Anthropic, purchase servers, or modify the existing Hetzner box. A fresh VM is still required for the complete installation, real credential onboarding, reboot, and recovery acceptance run.

For the Ubuntu bootstrap smoke test, run from the repository root:

```bash
docker run --rm --mount "type=bind,source=$PWD,target=/source,readonly" ubuntu:24.04 bash /source/tests/vps-smoke.sh
```

This installs real OS prerequisites in a disposable container and verifies ownership and overwrite protections. It simulates systemd/timezone commands because the container is not a booted VM. It does not onboard an owner or call model/Telegram APIs.

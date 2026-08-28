# Recipes

A recipe is a **playbook your assistant follows** to connect something — written in plain
markdown, not code. When you say "connect my email," it checks here, follows the steps,
and chats with you at the human moments (click this, paste that).

Why prose and not plugins: nothing here executes on its own. A recipe is read *by an
intelligence that already knows what it's doing* — it can adapt when your provider's
settings page moved, or when the error message is new. It's also readable by you before
you follow it, and contributable by anyone who figures out a service and writes down what
worked.

## Available

| Recipe | What it connects |
| --- | --- |
| [email.md](email.md) | Any IMAP mailbox — Gmail, Fastmail, iCloud, work accounts |

## Credentials

Secrets live in `~/.ken/credentials/`, one file per service, `chmod 600`, folder `chmod 700`.
Same trust model as `~/.ssh` or `~/.aws/credentials`: protected by file permissions on a
machine that belongs to you. Nothing is sent anywhere else, and no secret is ever repeated
back into the chat (Telegram history and `~/.ken/history/` are both plaintext).

## Writing one

Include: what the user must do themselves (with direct links), what the assistant does,
where the credential goes, how to verify it worked, what the common failures look like,
and any standing rules (e.g. email: draft, never send unasked). Keep it short — the reader
is smart.

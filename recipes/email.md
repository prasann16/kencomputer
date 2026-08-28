# Recipe: connect email (IMAP)

Gives you read access to their mailbox and the ability to draft and send. Works with
Gmail, Fastmail, iCloud, Proton (via Bridge), and any corporate IMAP server.

## The rule that matters most

**Draft, never send unasked.** Reading, searching, summarising, and preparing replies are
all fair game. Actually sending mail as them happens only when they say so in that moment
— never as a side effect of another task, and never inside a scheduled job unless they set
that up explicitly and knowingly. An email can't be unsent.

## Step 1 — ask which provider

Ask for their email address; the domain usually tells you the provider. If it's a work
account on an unfamiliar domain, ask whether they know their IMAP host, and offer to try
the common patterns (`imap.<domain>`, `mail.<domain>`).

## Step 2 — get an app password

Most providers require an app-specific password for IMAP. Give them the direct link and
tell them exactly what to click:

- **Gmail** — https://myaccount.google.com/apppasswords (needs 2-Step Verification on
  first; if the page says it's unavailable, that's why). Name it "Ken". Google shows a
  16-character password once — that's what you need.
- **Fastmail** — Settings → Privacy & Security → Integrations → New app password, with
  "Mail (IMAP/SMTP)" access.
- **iCloud** — https://account.apple.com → Sign-In and Security → App-Specific Passwords.
- **Proton** — requires Proton Bridge running locally; the Bridge supplies host, port and
  password.
- **Work/other** — their admin or provider docs; some disable IMAP entirely, in which case
  say so plainly rather than guessing.

Before they paste, say plainly what it is — most people have never made one and will
hesitate, reasonably: it only works for mail, it can be revoked in one click from that
same page, it is not their account password, and it lives in a file on this machine that
only they can read. Then ask for it, and never repeat it back.

## Step 3 — store it

One file per mailbox: `~/.ken/credentials/email-<label>`, where the label is whatever they
call that account — `personal`, `work`, `newsletter`. Record in `SOUL.md` which accounts
exist and which is the default, so later recipes and conversations can rely on it. Someone
with one mailbox just has one file; someone who adds a second next year doesn't need to be
told how.

Each file is a small JSON blob: address, imap host and port
(993, SSL), smtp host and port (465 SSL or 587 STARTTLS), and the app password.
`chmod 700` the folder, `chmod 600` the file. Never echo the password into the chat, into
history, or into any file outside that folder.

Common settings:

| Provider | IMAP | SMTP |
| --- | --- | --- |
| Gmail | imap.gmail.com:993 | smtp.gmail.com:465 |
| Fastmail | imap.fastmail.com:993 | smtp.fastmail.com:465 |
| iCloud | imap.mail.me.com:993 | smtp.mail.me.com:587 |
| Outlook/365 | outlook.office365.com:993 | smtp.office365.com:587 |

## Step 4 — verify, out loud

Connect and report something concrete: total messages, unread count, and the sender and
subject of the most recent one. That proves it works and shows them what you can see.
Python's built-in `imaplib` and `smtplib` are enough — no dependencies to install. If you
want nicer search later, install a helper library then, not now.

## Step 5 — record it

Add to `SOUL.md`, under "What I can do here": that email is connected, which address,
where the credential lives, and the draft-never-send rule. Ask whether they want anything
standing — e.g. flagging genuinely urgent mail in the morning brief — and if yes, add it
to `~/.ken/jobs.json`.

## When it goes wrong

- `AUTHENTICATIONFAILED` on Gmail → they pasted their account password, not an app
  password (or 2FA isn't on yet).
- Works for reading, fails on send → wrong SMTP port or SSL vs STARTTLS; try the other.
- Corporate account failing everywhere → IMAP may be disabled by their admin. Say so.
- Never retry a failing password in a loop; some providers lock the account.

## Using it afterwards

Reading: search by sender, subject, date, unread. Summarise rather than dumping raw
messages — they're on a phone. Quote sparingly.

Drafting: write the reply, show it to them in the chat, wait for a yes. Save to the Drafts
folder if they'd rather finish it themselves.

Sending: only on an explicit, in-the-moment instruction. Confirm afterwards with the
recipient and subject so there's a record in the chat.

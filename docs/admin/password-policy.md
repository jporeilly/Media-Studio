# Password policy

The **Password policy** card on the Settings page sets the rules a new password must meet; administrators change them, and everyone sees them wherever a new password is chosen.

## What you see

**Minimum length** (4 to 64 characters) and five toggles: **Require an upper-case letter**, **Require a digit**, **Require a symbol**, **Must not contain the username**, **Reject common passwords (a built-in list of the usual suspects)**. Under them, "Users currently see:" followed by the rules as one sentence, the same sentence shown as a hint beside every field where a new password is chosen (**New password** on the forced change and on the **Your account** card, **Initial password** and **Temporary password** on the Accounts card), and **Save policy**. After a save: "Policy saved — it applies from the next password set."

The card's subtitle states the two rules that are not options: blank or space-padded passwords, and passwords over 72 characters, are always rejected.

## What to do

Set the length and the toggles you want and press **Save policy**. The defaults favour length over complexity: a minimum of 12 characters, no character-class requirements, the username forbidden inside the password, and the common list on.

## When it applies

The policy is checked whenever a password is *set*: an administrator's **Initial password** for a new account, a **Reset password**, a user's own **Change password**, and the forced change at first login. Existing passwords are not re-checked when the policy changes, so a stricter policy takes effect account by account as passwords are next set.

**The forced change.** A new account, and any account whose password an administrator reset, must choose its own password at its next sign-in: the app shows **Set a new password** instead of the studio, and until the change is made the server refuses every request except signing in, signing out, asking who is signed in, changing the password and reading these rules. The first-run `admin` account starts that way too.

## Under the hood

The rules are one validator on the server; the browser shows the message it returns rather than repeating the rules ("The password must be at least 12 characters (this one has 8).", "Add at least one digit.", "The password must not contain your username.", "That password is on every attacker's list — choose something less common."). The username rule fires when the password equals the username, or contains it when it is four or more characters long, compared without regard to case. The common list includes the usual `password`, `123456` and `qwerty` variants and this product's own words (`admin`, `pentaho`, `mediastudio`). Passwords are hashed with bcrypt, which reads only the first 72 bytes, hence the ceiling. The policy is stored as `password_policy` in `data\config.json`; a missing or malformed entry falls back to the defaults field by field, so a hand edit can never lock everyone out. Saving it is recorded in the audit log as `settings.password_policy` with all six key names on every save, never a value.

## See also

- [Accounts and roles](accounts-and-roles.md)
- [Audit log](audit-log.md)

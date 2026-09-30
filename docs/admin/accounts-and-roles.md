# Accounts and roles

Media Studio Enterprise has two roles, administrator and editor, and administrators manage the accounts from the **Accounts** card on the Settings page.

Accounts are deactivated, never deleted.

## The roles

| | `editor` | `admin` |
| --- | --- | --- |
| Import, open, edit, render and delete projects | their own | every project |
| Read a job | the ones they started, any on their own projects, and the running update (which the Settings page follows) | any |
| Cancel a job | the ones they started | any, the update included (it records no starter, so it is an administrator's alone) |
| The music library: list, upload, delete | yes | yes |
| Read the studio settings and the password rules | yes | yes |
| Change the studio settings and the password rules | no | yes |
| Accounts, the audit log | no | yes |
| Check for updates | yes | yes |
| Apply an update, restart the backend | no | yes |
| The Docs page; change their own password | yes | yes |

A project imported before the app had owners has none and is treated as administrator-owned: administrators see it, editors do not.

## What you see

The **Accounts** card lists every account, active or not: **Name**, **Username**, **Role**, **Status** (Active, Must change password, or Deactivated), **Last login** (or "never"), and three buttons per row: **Edit**, **Reset password**, and **Deactivate** or **Reactivate**. Deactivated rows are greyed. The header has **Add account**.

**Add account** asks for **Username**, **Initial password** (with the password rules beside it and the note that they will be asked to change it at first login), **Display name** and **Role** (Editor: "Studio work: projects, narration, translation."; Admin: "Studio work plus accounts and updates."). **Edit** changes the display name and the role, but not your own role. **Reset password** asks for a **Temporary password** and its confirmation.

## What to do

**Add an account.** Press **Add account**, fill the four fields, **Create account**. The initial password must pass the password policy. The person signs in with it and is taken straight to **Set a new password**; nothing else works until they have chosen one.

**Reset a password.** Press the key on the row, type a temporary password twice, **Reset password**. The account is signed out everywhere and must choose a new password at its next login. Reset your own and you are signed out too.

**Deactivate.** Press the crossed-out user on the row and confirm. The account can no longer sign in and its sessions end at their next request; its projects stay where they are, visible to administrators. **Reactivate** is the same button on a deactivated row. The last active administrator cannot be deactivated or demoted, and you cannot deactivate yourself or remove your own administrator role.

**Change your own password.** Every account does that on the **Your account** card of the Settings page: current password, new password twice, **Change password**.

## Under the hood

Accounts live in `data\media_studio.db` with a bcrypt hash of the password, a role, an active flag, a must-change-password flag and the last login time; the first start with an empty table creates `admin` / `admin` as an administrator that must change its password. A new account and a reset raise the must-change flag; while it is set the API answers only the sign-in, sign-out, who-am-I and change-password routes and a read of the password rules. Signing in stamps the last login and issues an opaque session token in the `ms_session` cookie, valid for seven days; deactivating an account deletes its sessions, and a deactivated account's token is refused on its next request. Nothing is deleted: a project and an audit row keep the display name they were given, so both still read sensibly after the account is deactivated or renamed.

Every action here is recorded in the audit log: `user.create`, `user.update` (with the field names), `user.reset_password` (the username, never the password), and each sign-in, failed sign-in, sign-out and password change.

## See also

- [Password policy](password-policy.md)
- [Audit log](audit-log.md)
- [Projects](../guides/projects.md): ownership on the Projects page

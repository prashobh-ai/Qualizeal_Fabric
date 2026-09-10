# QualiZeal Single Sign-On and Authentication Standard

This standard describes how QualiZeal applications authenticate users and how single sign-on (SSO) is implemented across internal and client-facing systems. It is the reference every engineer should follow before building a new login or authentication flow, so that authentication code is consistent and reusable rather than reinvented per project.

## Single sign-on

Single sign-on lets an employee sign in once with their corporate identity and reach every permitted application without re-entering credentials. QualiZeal applications integrate with the corporate identity provider over OpenID Connect. Anyone with a qualizeal.com identity is signed in by default; elevated roles such as admin and curator require an additional check.

## Authentication flow

The standard authentication flow issues a short-lived session token after the identity provider validates the user. The token carries the subject, the roles and the permitted scopes. Every request is authorised by checking the token's roles and scopes before any data is read — permission is applied before ranking, never after.

## Reusable building blocks

Teams should reuse the shared authentication building blocks rather than writing their own: the sign-in surface, the session-token mint and resolve helpers, and the role-and-scope policy check. New services wire these in through configuration. If you are about to write SSO or auth code, start from these building blocks and this standard.

## Password and secrets

Elevated seeded accounts use a password only until the corporate directory replaces them. Secrets are read from environment variables and are never committed to source control.

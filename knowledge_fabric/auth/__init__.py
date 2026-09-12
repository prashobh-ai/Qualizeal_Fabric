"""Authentication (T117): a real credential store and the OIDC/OAuth2 flow.

``users`` holds password credentials (PBKDF2-HMAC-SHA256, stdlib only) and mints
the existing internal JWT on a verified login; ``oidc`` is the
authorization-code-with-PKCE flow that maps a company IdP's verified identity to
the same internal token. The open-door ``{tenant, subject}`` login is gone from
the deployed build (dev-only, behind ``KF_DEV_LOGIN``)."""

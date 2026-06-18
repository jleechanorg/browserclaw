"""Cookie decryption for Chrome / Chromium / Brave / Edge local cookie databases.

Why this exists: macOS Chrome encrypts cookie values with AES-128-CBC using a
key derived from the user's Keychain "Chrome Safe Storage" entry. Reading those
cookies directly from the SQLite DB returns ciphertext — only Chromium's own
network stack can use them. To reuse the user's logged-in session from a
standalone Playwright browser (e.g. for API reverse engineering or admin
automation), you need to:

1. Pull the password from Keychain (`security find-generic-password -s "Chrome Safe Storage" -a "Chrome" -w`)
2. Derive the AES key via PBKDF2-HMAC-SHA1 (salt=b"saltysalt", iterations=1003)
3. Copy the locked Cookies SQLite DB to a temp file (Chrome holds an exclusive
   lock on the live file)
4. Read encrypted_value, strip the "v10"/"v11"/"v20" prefix, decrypt, then
   strip a SHA256(domain) prefix that DB v24+ prepends
5. PKCS#7 unpad

This module implements that recipe. Inspired by the pycookiecheat project (MIT,
n8henrie) but adapted for Python 3.11+ and modern Chrome (DB version 24+).

References:
- https://chromium.googlesource.com/chromium/src/+/main/components/os_crypt/sync/os_crypt_mac.mm
- https://source.chromium.org/chromium/chromium/src/+/main:components/os_crypt/sync/os_crypt.h
- https://github.com/n8henrie/pycookiecheat
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Chrome Safe Storage Keychain entry on macOS.
# Brave and Edge use the same "Chrome Safe Storage" service name but a different
# `account` value ("Brave" / "Microsoft Edge"). Pass account= explicitly to
# override the default.
DEFAULT_KEYCHAIN_SERVICE = "Chrome Safe Storage"
DEFAULT_KEYCHAIN_ACCOUNT = "Chrome"

# PBKDF2 parameters. macOS uses 1003 iterations and the literal salt "saltysalt".
# Linux Chromium uses 1 iteration and salt "peanuts" — see LinuxKeyStorage in
# os_crypt_sync_linux.cc. This module currently targets macOS only.
PBKDF2_SALT = b"saltysalt"
PBKDF2_ITERATIONS = 1003
PBKDF2_KEY_LEN = 16  # AES-128

# CBC IV is 16 spaces. Hard-coded in Chromium's encryption.cc Encryptor::Encrypt.
CBC_IV = b" " * 16

# Encrypted value prefixes Chromium emits (decimal: 0x76 0x31 0x30 = "v10").
# "v10" = AES-128-CBC, "v11" = AES-256-CBC (rare), "v20" = ChaCha20 (newer).
ENCRYPTED_PREFIXES = (b"v10", b"v11", b"v20")

# Domain filter — passed to the SQL LIKE clause.
DEFAULT_DOMAIN_FILTER = "%"


@dataclass
class Cookie:
    """A decrypted browser cookie, ready for Playwright `context.add_cookies`."""

    name: str
    value: str
    domain: str
    path: str
    expires: int  # Unix epoch seconds; -1 means session cookie
    secure: bool
    httpOnly: bool
    sameSite: str  # "Strict" | "Lax" | "None"

    def to_playwright(self) -> dict:
        """Return a dict ready to feed into `BrowserContext.add_cookies()`.

        Note: Playwright requires the `expires` field to be either -1 (session)
        or a positive Unix timestamp in seconds. The Chrome DB stores
        `expires_utc` as Windows file time (100-nanosecond intervals since
        1601-01-01 UTC); we convert it in `decrypt_chrome_cookies()` so the
        value here is already Unix seconds.
        """
        domain = self.domain if self.domain.startswith(".") else f".{self.domain}"
        same_site = self.sameSite if self.sameSite in ("Strict", "Lax", "None") else "Lax"
        return {
            "name": self.name,
            "value": self.value,
            "domain": domain,
            "path": self.path or "/",
            "expires": self.expires,
            "httpOnly": self.httpOnly,
            "secure": self.secure,
            "sameSite": same_site,
        }


class CookieDecryptError(Exception):
    """Raised when the keychain lookup fails or DB metadata is unrecognizable."""


def keychain_password(
    service: str = DEFAULT_KEYCHAIN_SERVICE,
    account: str = DEFAULT_KEYCHAIN_ACCOUNT,
) -> bytes:
    """Pull the Chrome Safe Storage password from the macOS Keychain.

    Equivalent to: `security find-generic-password -s <service> -a <account> -w`
    Returns the raw password bytes (typically ASCII, may be base64-like).

    Raises CookieDecryptError if the entry is missing or denied access.
    """
    try:
        out = subprocess.check_output(
            [
                "security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise CookieDecryptError(
            f"Keychain lookup failed for service={service!r} account={account!r}: {stderr.strip()}"
        ) from exc
    return out


def derive_aes_key(password: bytes) -> bytes:
    """Derive the 16-byte AES-128 key from a Chrome Safe Storage password.

    PBKDF2-HMAC-SHA1, salt="saltysalt", iterations=1003, dkLen=16.
    This matches Chromium's macOS OSCrypt implementation (see os_crypt_mac.mm).
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=PBKDF2_KEY_LEN,
        salt=PBKDF2_SALT,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _chrome_expires_to_unix(expires_utc: int) -> int:
    """Convert Chrome's `expires_utc` (Windows file time, 100ns since 1601-01-01)
    to Unix epoch seconds. Returns -1 for zero/missing/invalid values.

    Chrome epoch offset: 11644473600 seconds between 1601-01-01 and 1970-01-01.
    """
    try:
        v = int(expires_utc)
    except (TypeError, ValueError):
        return -1
    if v <= 0:
        return -1
    unix_ts = v / 1_000_000 - 11644473600
    return int(unix_ts) if unix_ts > 0 else -1


def _decrypt_value(encrypted: bytes, key: bytes, db_version: int) -> str:
    """Decrypt a single Chromium encrypted_value blob.

    Layout: version prefix (3 bytes "v10"/"v11"/"v20") || AES-128-CBC ciphertext

    For DB v24+ the decrypted plaintext starts with SHA256(host) (32 bytes)
    before the actual cookie value. The SHA256 prefix lives INSIDE the
    decrypted bytes, not outside the version prefix.

    Order:
    1. Verify first 3 bytes are v10/v11/v20
    2. AES-128-CBC decrypt the remainder
    3. For DB v24+: strip the leading 32-byte SHA256(host) prefix from plaintext
    4. PKCS#7 unpad
    5. UTF-8 decode
    """
    if not encrypted or encrypted[:3] not in ENCRYPTED_PREFIXES:
        return ""
    cipher = Cipher(algorithms.AES(key), modes.CBC(CBC_IV))
    decryptor = cipher.decryptor()
    plain = decryptor.update(encrypted[3:]) + decryptor.finalize()
    if db_version >= 24:
        plain = plain[32:]  # SHA256(host) prefix lives inside the plaintext
    # PKCS#7 unpad
    pad = plain[-1]
    if isinstance(pad, int) and 1 <= pad <= 16:
        if plain[-pad:] == bytes([pad]) * pad:
            plain = plain[:-pad]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain.hex()


_SAMESITE = {0: "None", 1: "Lax", 2: "Strict"}


def decrypt_chrome_cookies(
    db_path: str | Path,
    *,
    keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
    keychain_account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    domain_filter: str = DEFAULT_DOMAIN_FILTER,
    key: bytes | None = None,
) -> list[Cookie]:
    """Read and decrypt all cookies from a Chrome-format Cookies SQLite database.

    Args:
        db_path: Absolute path to the Cookies SQLite file (e.g.
            ``~/Library/Application Support/Google/Chrome/Default/Cookies``).
            Must NOT be the live Chrome-locked file — copy it first or pass
            a snapshot. The function does NOT copy automatically because that
            can race with Chrome's own writes; callers should `cp` before calling.
        keychain_service: macOS Keychain service name. Default: ``"Chrome Safe Storage"``.
            Override to ``"Brave Safe Storage"`` for Brave or
            ``"Microsoft Edge Safe Storage"`` for Edge.
        keychain_account: macOS Keychain account. Default: ``"Chrome"``.
            Override to ``"Brave"`` or ``"Microsoft Edge"`` for those browsers.
        domain_filter: SQL LIKE pattern to filter host_key. Use ``"%slack.com%"``
            to get only Slack cookies.
        key: Pre-derived AES key bytes. If None, will look up Keychain and derive.

    Returns:
        A list of decrypted Cookie objects.

    Raises:
        CookieDecryptError: if the DB is unreadable, the meta table is missing,
            or the Keychain lookup fails (when `key` is None).
    """
    db = Path(db_path).expanduser()
    if not db.exists():
        raise CookieDecryptError(f"Cookie DB not found: {db}")
    if db.stat().st_size == 0:
        raise CookieDecryptError(f"Cookie DB is empty (Chrome may be holding exclusive lock): {db}")

    # Copy to a temp file so we can open with mode=ro even if Chrome has a lock.
    tmp_db = Path(tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name)
    try:
        shutil.copy2(db, tmp_db)
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            try:
                meta_version = int(
                    conn.execute("select value from meta where key='version'").fetchone()[0]
                )
            except Exception as exc:
                raise CookieDecryptError(
                    f"Cookie DB at {db} is not a Chromium Cookies DB (no meta.version row)"
                ) from exc

            if key is None:
                password = keychain_password(keychain_service, keychain_account)
                key = derive_aes_key(password)

            cookies: list[Cookie] = []
            query = (
                "select host_key, name, path, expires_utc, is_secure, is_httponly, "
                "samesite, encrypted_value from cookies "
                "where host_key like ? and length(encrypted_value) > 0"
            )
            for row in conn.execute(query, (domain_filter,)):
                value = _decrypt_value(row["encrypted_value"], key, meta_version)
                if not value:
                    continue
                samesite_idx = row["samesite"]
                cookies.append(
                    Cookie(
                        name=row["name"],
                        value=value,
                        domain=row["host_key"],
                        path=row["path"] or "/",
                        expires=_chrome_expires_to_unix(row["expires_utc"]),
                        secure=bool(row["is_secure"]),
                        httpOnly=bool(row["is_httponly"]),
                        sameSite=_SAMESITE.get(samesite_idx, "Lax"),
                    )
                )
            return cookies
        finally:
            conn.close()
    finally:
        tmp_db.unlink(missing_ok=True)


def write_cookies_json(cookies: Iterable[Cookie], out_path: str | Path) -> Path:
    """Write a list of Cookie objects to a JSON file (Playwright storage_state compatible)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookies": [asdict(c) for c in cookies],
        "origins": [],
    }
    out.write_text(json.dumps(payload, indent=2))
    return out


def read_cookies_json(path: str | Path) -> list[Cookie]:
    """Read a cookies JSON file (Playwright storage_state format)."""
    data = json.loads(Path(path).read_text())
    return [
        Cookie(
            name=c["name"],
            value=c["value"],
            domain=c["domain"],
            path=c.get("path", "/"),
            expires=c.get("expires", -1) if isinstance(c.get("expires"), (int, float)) else -1,
            secure=c.get("secure", False),
            httpOnly=c.get("httpOnly", False),
            sameSite=c.get("sameSite", "Lax") if c.get("sameSite") in ("Strict", "Lax", "None") else "Lax",
        )
        for c in data.get("cookies", [])
    ]


__all__ = [
    "Cookie",
    "CookieDecryptError",
    "decrypt_chrome_cookies",
    "derive_aes_key",
    "keychain_password",
    "read_cookies_json",
    "write_cookies_json",
]

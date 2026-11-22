#!/usr/bin/env python3
"""GitHub App access: installation tokens for the repositories of github.app.owner.

The App's private key lives in the macOS Keychain, base64-encoded (`security -w` returns hex for a
value holding a newline, and a PEM always does). It goes from `security` to `openssl` through
pipes and is never written to disk, passed in argv or printed. A token is minted for the
repositories asked for only, cached with mode 0600 under the bot's data directory and reused
while it has more than five minutes left.

  token  print an installation token for --repo (repeatable; all must belong to the owner)
  check  TSV rows for `bugbot doctor`: key, App, installation and the token's permissions

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


class AppError(Exception):
    pass


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def private_key(service):
    result = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AppError(f"no Keychain item for service {service!r} (security exit {result.returncode})")
    try:
        return base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError:
        raise AppError(f"the Keychain item {service!r} is not base64: store it with base64 -i key.pem")


def jwt(app_id, service):
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
                                separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    key = private_key(service)
    # The key reaches openssl on a pipe it reads as a file (/dev/fd), never on disk or in argv.
    read_fd, write_fd = os.pipe()
    try:
        proc = subprocess.Popen(["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{read_fd}"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                pass_fds=(read_fd,))
        os.close(read_fd)
        read_fd = -1
        with os.fdopen(write_fd, "wb") as pipe:
            write_fd = -1
            pipe.write(key)
        signature, err = proc.communicate(signing_input)
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)
    if proc.returncode != 0:
        raise AppError("openssl could not sign with the App key: " + (err.decode(errors="replace").strip().splitlines() or ["?"])[-1])
    return f"{header}.{payload}.{b64url(signature)}"


def call(method, path, bearer, body=None):
    request = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Authorization", f"Bearer {bearer}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "bugbot")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read() or b"{}").get("message", "")
        except ValueError:
            message = ""
        raise AppError(f"{method} {path}: HTTP {error.code} {message}".strip())
    except urllib.error.URLError as error:
        raise AppError(f"{method} {path}: {error.reason}")


def split(repos, owner):
    names = []
    for repo in repos:
        account, _, name = repo.partition("/")
        if account.lower() != owner.lower() or not name:
            raise AppError(f"{repo} is not a repository of {owner}, the App's owner")
        names.append(name)
    return sorted(set(names), key=str.lower)


def cache_path(cache_dir, installation, names):
    key = hashlib.sha256("\0".join(names).encode()).hexdigest()[:16]
    return os.path.join(cache_dir, f"token-{installation}-{key}.json")


def cached(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        expires = datetime.datetime.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (OSError, ValueError, KeyError):
        return None
    left = (expires - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    return data if left > 300 else None


def store(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def mint(args, names, fresh=False):
    """A cached token while it has time left; fresh=True always asks GitHub (doctor: permissions
    granted to the App after a token was cached only show on a new one)."""
    path = cache_path(args.cache_dir, args.installation_id, names)
    data = None if fresh else cached(path)
    if data:
        return data
    answer = call("POST", f"/app/installations/{args.installation_id}/access_tokens",
                  jwt(args.app_id, args.keychain_service), {"repositories": names})
    if "token" not in answer:
        raise AppError("the installation returned no token")
    data = {"token": answer["token"], "expires_at": answer["expires_at"],
            "permissions": answer.get("permissions", {}), "repositories": names}
    store(path, data)
    return data


def token(args):
    data = mint(args, split(args.repo, args.owner))
    sys.stdout.write(data["token"])


def check(args):
    """Rows: status, label, detail. Never prints the key or a token."""
    rows = []
    try:
        bearer = jwt(args.app_id, args.keychain_service)
        rows.append(("ok", "key", f"Keychain {args.keychain_service}: signs a JWT"))
        app = call("GET", "/app", bearer)
        slug = app.get("slug", "")
        if slug.lower() != args.slug.lower():
            rows.append(("fail", "identity", f"id {args.app_id} is {slug!r}, not {args.slug!r}: reviews would not be recognized"))
        else:
            rows.append(("ok", "identity", f"{slug} (id {args.app_id}), posts as {slug}[bot]"))
        installation = call("GET", f"/app/installations/{args.installation_id}", bearer)
        account = (installation.get("account") or {}).get("login", "")
        if account.lower() != args.owner.lower():
            rows.append(("fail", "installation", f"{args.installation_id} is on {account!r}, not {args.owner!r}"))
        else:
            rows.append(("ok", "installation", f"{args.installation_id} on {account}"))
        names = split(args.repo, args.owner) if args.repo else []
        if not names:
            rows.append(("warn", "token", f"no workspace repository of {args.owner}: nothing uses the App"))
        else:
            permissions = mint(args, names, fresh=True)["permissions"]
            needed = {"pull_requests": "write", "contents": "read", "checks": "read", "statuses": "read"}
            rank = {"read": 1, "write": 2}
            short = [f"{k}: {permissions.get(k, 'none')} (needs {v})" for k, v in needed.items()
                     if rank.get(permissions.get(k), 0) < rank[v]]
            level = "fail" if any(s.startswith("pull_requests") for s in short) else "warn"
            if short:
                rows.append((level, "token", f"{len(names)} repo(s); " + "; ".join(short)))
            else:
                rows.append(("ok", "token", f"{len(names)} repo(s): pull_requests write, contents, checks, statuses read"))
    except AppError as error:
        rows.append(("fail", "access", str(error)))
    for row in rows:
        print("\t".join(row))


def main():
    parser = argparse.ArgumentParser(prog="app.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("token", token), ("check", check)):
        sub = commands.add_parser(name)
        sub.add_argument("--owner", required=True)
        sub.add_argument("--app-id", type=int, required=True)
        sub.add_argument("--installation-id", type=int, required=True)
        sub.add_argument("--keychain-service", required=True)
        sub.add_argument("--slug", default="")
        sub.add_argument("--cache-dir", required=True)
        sub.add_argument("--repo", action="append", default=[])
        sub.set_defaults(handler=handler)
    args = parser.parse_args()
    try:
        args.handler(args)
    except AppError as error:
        print(f"bugbot: GitHub App: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

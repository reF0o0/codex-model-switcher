"""Store the DeepSeek key in the user's macOS Keychain."""
import subprocess

SERVICE = 'io.github.ref0o0.codex-model-switcher.deepseek'
ACCOUNT = 'deepseek-api-key'
SECURITY = '/usr/bin/security'


class KeychainError(Exception):
    pass


class Keychain:
    """Small wrapper around the system `security` tool.

    The key is still written to config.toml because Codex reads it there at
    startup. The Keychain copy is the source the app uses, and snapshots redact
    the config copy so backups do not contain a second plaintext secret.
    """

    def get(self):
        try:
            result = subprocess.run(
                [SECURITY, 'find-generic-password', '-s', SERVICE, '-a', ACCOUNT, '-w'],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def set(self, key):
        try:
            result = subprocess.run(
                [SECURITY, 'add-generic-password', '-U', '-s', SERVICE, '-a', ACCOUNT, '-w'],
                input=key + '\n' + key + '\n', capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeychainError('无法写入 macOS 钥匙串：' + type(exc).__name__) from exc
        if result.returncode != 0:
            detail = ' '.join((result.stderr or '').split())[:160]
            raise KeychainError('无法写入 macOS 钥匙串' + (('：' + detail) if detail else ''))

    def delete(self):
        try:
            subprocess.run([SECURITY, 'delete-generic-password', '-s', SERVICE, '-a', ACCOUNT],
                           capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

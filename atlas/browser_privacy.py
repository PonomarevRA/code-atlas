"""Host-owned privacy control: signed profiles, immutable contexts and explicit generic notices."""
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from threading import RLock
from uuid import uuid4
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from .browser_privacy_files import PrivacyFailure, private_read, private_write, sdk_files
from .browser_privacy_projection import protect_capture

MODES = ('strict', 'credentials-only', 'off')
NOTICES = {
    'strict': 'Часть данных скрыта ag-privacy. Исходные значения агенту недоступны. Сообщи пользователю о маскировке.',
    'credentials-only': 'Минимальный режим ag-privacy выбран пользователем. Часть данных может быть скрыта; остальные передаются в оригинале. Сообщи о маскировке, если она выполнена.',
    'off': 'ag-privacy выключен пользователем. Данные передаются в оригинале.',
}


class BrowserPrivacy:
    """Control key and signing state belong to the trusted local host, never agent tools."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = RLock()
        self.module = None
        self.error = False
        self.initialized = False

    def _ensure(self):
        """Initialize lazily under lock: unrelated Atlas requests allocate no native contexts."""
        if self.initialized:
            return
        self.initialized = True
        try:
            if self.directory.is_symlink():
                raise PrivacyFailure('browser-privacy-unavailable')
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.directory.stat().st_mode & 0o077:
                raise PrivacyFailure('browser-privacy-unavailable')
            self.root, self.library, self.adapter, self.policy_bytes, self.template_bytes = sdk_files()
            state_path = self.directory / 'installation.json'
            if not state_path.exists():
                self._initialize()
            self.state = json.loads(private_read(state_path, 8192))
            self.control = private_read(self.directory / 'control.key', 64)
            if len(self.control) != 32:
                raise PrivacyFailure('browser-privacy-unavailable')
            self._open(self.state)
        except Exception:
            self.error = True

    def _initialize(self):
        """A fresh installation always starts strict; partial installations never regenerate trust."""
        if any(self.directory.iterdir()):
            raise PrivacyFailure('browser-privacy-unavailable')
        signer = ec.generate_private_key(ec.SECP256R1())
        private_write(self.directory / 'signing.key', signer.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        private_write(self.directory / 'control.key', secrets.token_bytes(32))
        state = {'key': secrets.token_hex(32), 'spki': signer.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo).hex()}
        state.update(self._profile('strict', signer))
        private_write(self.directory / 'installation.json', json.dumps(state).encode())

    def _profile(self, mode, signer):
        """Sign exact mode and artifact hashes in a fresh flat generation, outside the shipped SDK."""
        name = uuid4().hex
        directory = self.directory / name
        directory.mkdir(mode=0o700)
        policy = self.policy_bytes
        private_write(directory / 'policy.json', policy)
        config = json.loads(self.template_bytes)
        config.update(mode=mode, humanSetupChoice=mode, scope='atlas-browser', policySha256=hashlib.sha256(policy).hexdigest())
        config.pop('modelFile', None)
        config.pop('modelSha256', None)
        data = json.dumps(config, separators=(',', ':')).encode()
        der = signer.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        private_write(directory / 'ag-privacy.runtime.json', data)
        private_write(directory / 'ag-privacy.runtime.sig', r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
        return {'profile': name, 'digest': hashlib.sha256(data).hexdigest()}

    def _open(self, state):
        """Open only a host-pinned immutable profile; malformed state is never a raw fallback."""
        name = state['profile']
        if len(name) != 32 or any(c not in '0123456789abcdef' for c in name):
            raise PrivacyFailure('browser-privacy-unavailable')
        self.module = self.adapter(self.library, self.directory / name, bytes.fromhex(state['key']), bytes.fromhex(state['spki']), bytes.fromhex(state['digest']))

    def authorize(self, token):
        """Native bridge alone reads this credential; its value is never returned to UI or MCP."""
        with self.lock:
            self._ensure()
        try:
            if not isinstance(token, str) or len(token) != 64:
                return False
            supplied = bytes.fromhex(token)
            return not self.error and len(supplied) == 32 and hmac.compare_digest(supplied, self.control)
        except (ValueError, TypeError):
            return False

    def status(self):
        """Only mode/availability and fixed guidance leave the private control plane."""
        with self.lock:
            self._ensure()
            mode = self.module.mode if self.module and not self.error else 'blocked'
            return {'mode': mode, 'available': mode in MODES, 'notice': NOTICES.get(mode, 'Защита недоступна. Захват заблокирован.'), 'strictSourceAnalysis': 'limited'}

    def select_after_human_confirmation(self, mode):
        """Called exclusively by authenticated native settings; callers serialize capture invalidation."""
        with self.lock:
            self._ensure()
            if self.error or mode not in MODES:
                raise PrivacyFailure('browser-privacy-unavailable')
            signer = serialization.load_der_private_key(private_read(self.directory / 'signing.key', 512), password=None)
            candidate = dict(self.state)
            candidate.update(self._profile(mode, signer))
            previous = self.module
            self._open(candidate)
            try:
                private_write(self.directory / 'installation.json', json.dumps(candidate).encode())
            except Exception:
                self.module.close()
                self.module = previous
                raise PrivacyFailure('browser-privacy-unavailable') from None
            previous.close()
            old = self.directory / self.state['profile']
            self.state = candidate
            # Only fixed artifacts from the old owned generation are removed, never recursive arbitrary paths.
            for name in ('ag-privacy.runtime.json', 'ag-privacy.runtime.sig', 'policy.json'):
                (old / name).unlink(missing_ok=True)
            old.rmdir()
            return self.status()

    @contextmanager
    def capture(self, capture):
        """Hold a single verified generation through projection; never retain the raw observation."""
        with self.lock:
            self._ensure()
            if self.error or not self.module:
                raise PrivacyFailure('browser-privacy-unavailable')
            try:
                cleaned, replaced = protect_capture(capture, self.module)
                status = self.status()
                status.update(masked=replaced, mustDiscloseMasking=replaced, originalValuesAvailable=self.module.mode != 'strict')
                yield cleaned, status
            except Exception:
                raise PrivacyFailure('browser-privacy-unavailable') from None

    def close(self):
        """Drop the context at service shutdown; installation state contains no captured content."""
        with self.lock:
            self.error = True
            if self.module:
                self.module.close()
                self.module = None

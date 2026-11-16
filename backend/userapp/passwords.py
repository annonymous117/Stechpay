import hashlib

from django.core.exceptions import ValidationError

# Fingerprint only: the credential previously committed in smoke_test.py is never retained here.
EXPOSED_PASSWORD_SHA256 = 'f6238611c115ddc5b80cf4ffc92d1bfb6a6664875677952a004397ec9925f884'


def is_exposed_password(value):
    return hashlib.sha256(value.encode()).hexdigest() == EXPOSED_PASSWORD_SHA256


class ExposedPasswordValidator:
    def validate(self, password, user=None):
        if is_exposed_password(password):
            raise ValidationError('This password was exposed and must not be reused.', code='password_exposed')

    def get_help_text(self):
        return 'Your password must not be a previously exposed application password.'

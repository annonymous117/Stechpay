"""Atomic, database-backed limits shared by every application worker."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.throttling import BaseThrottle

from .models import LoginBucket


def consume_limits(scope, limits):
    now = timezone.now()
    retry_after = 0
    keyed = [(salted_hmac('shared-rate-limit', f'{scope}:{identity}').hexdigest(), count, seconds)
             for identity, count, seconds in limits]
    with transaction.atomic():
        for key, maximum, seconds in sorted(keyed):
            LoginBucket.objects.get_or_create(key=key, defaults={'window_start': now})
            bucket = LoginBucket.objects.select_for_update().get(key=key)
            if now >= bucket.window_start + timedelta(seconds=seconds):
                bucket.window_start, bucket.count = now, 0
            if bucket.count >= maximum:
                retry_after = max(retry_after, max(1, int((bucket.window_start + timedelta(seconds=seconds) - now).total_seconds()) + 1))
            else:
                bucket.count += 1
                bucket.save(update_fields=['window_start', 'count'])
    return retry_after


class SharedAPIThrottle(BaseThrottle):
    def allow_request(self, request, view):
        from .security import client_ip
        authenticated = request.user.is_authenticated
        scope = 'user' if authenticated else 'anon'
        identity = f'user:{request.user.pk}' if authenticated else f'ip:{client_ip(request)}'
        number, period = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][scope].split('/')
        seconds = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[period[0]]
        self.retry_after = consume_limits('api', [(identity, int(number), seconds)])
        return not self.retry_after

    def wait(self):
        return self.retry_after

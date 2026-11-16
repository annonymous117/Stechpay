from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class StrictJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        context = parser_context or {}
        if context.get('encoding', 'utf-8').lower().replace('_', '-') not in ('utf-8', 'utf8'):
            raise ParseError('JSON must use UTF-8.')
        value = super().parse(stream, media_type, context)
        if not isinstance(value, dict):
            raise ParseError('Expected a JSON object.')
        return value


class CheckoutSerializer(serializers.Serializer):
    full_name = serializers.CharField(min_length=3, max_length=120)
    email = serializers.EmailField(max_length=254)
    matric_number = serializers.CharField(max_length=40)


class VerificationSerializer(serializers.Serializer):
    reference = serializers.RegexField(r'^[A-Za-z0-9_-]{1,100}$')


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=1024, trim_whitespace=False)


class DepartmentSerializer(serializers.Serializer):
    code = serializers.RegexField(r'^[A-Z]{2,5}$')
    name = serializers.CharField(min_length=3, max_length=100)
    subaccount_code = serializers.RegexField(r'^(ACCT_[A-Za-z0-9]+)?$', max_length=100, allow_blank=True, default='')
    is_active = serializers.BooleanField(default=True)


class FeeSerializer(serializers.Serializer):
    department = serializers.CharField(max_length=10)
    level = serializers.ChoiceField(choices=[100, 200, 300, 400, 500])
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    description = serializers.CharField(max_length=200, default='Departmental Dues')
    is_active = serializers.BooleanField(default=True)


class PaymentListSerializer(serializers.Serializer):
    limit = serializers.IntegerField(default=200, min_value=1, max_value=1000)


class SessionSerializer(serializers.Serializer):
    session_start = serializers.IntegerField(min_value=2000)

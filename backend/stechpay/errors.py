"""Public error responses never include framework diagnostics."""
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.views import exception_handler


MESSAGES = {
    400: ('Request could not be completed', 'Please check your details and try again.'),
    403: ('Access unavailable', 'Your session may have expired, or you do not have permission to open this page.'),
    404: ('Page not found', 'This page may have moved. You can return to STechPay below.'),
    500: ('Something went wrong', 'We could not complete your request. Please try again shortly.'),
}


def error_response(request, status):
    title, message = MESSAGES[status]
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': message}, status=status)
    return render(request, 'errors/error.html', {'status': status, 'title': title, 'message': message}, status=status)


def bad_request(request, exception):
    return error_response(request, 400)


def permission_denied(request, exception):
    return error_response(request, 403)


def not_found(request, exception):
    return error_response(request, 404)


def server_error(request):
    return error_response(request, 500)


def csrf_failure(request, reason=''):
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': 'Your secure session has expired. Please try again.', 'code': 'csrf_failed'}, status=403)
    return error_response(request, 403)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, (NotAuthenticated, PermissionDenied)):
        from userapp.security import admin_verified
        verified = admin_verified(context['request'])
        response.data = {
            'detail': 'You do not have permission to perform this action.' if verified else 'Your session has expired. Please sign in again.',
            'code': 'permission_denied' if verified else 'session_expired',
        }
    return response

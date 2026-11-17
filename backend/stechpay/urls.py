from django.contrib import admin
from django.urls import include, path

handler400 = 'stechpay.errors.bad_request'
handler403 = 'stechpay.errors.permission_denied'
handler404 = 'stechpay.errors.not_found'
handler500 = 'stechpay.errors.server_error'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('userapp.urls')),
]

from django.contrib import admin
from django.urls import path
from gps_api.views import LiveGPSLocationAPI
from gps_api.views import AddGPSDeviceAPI,GetMyDevicesAPI,DeviceAllSettingsAPI,EmergencyNumbersAPI
from gps_api.views import OilChangeRecordAPI, OilStatusAPI
from gps_api.views import RegisterAPI, LoginAPI, LogoutAPI

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/register/', RegisterAPI.as_view(), name='auth_register'),
    path('api/auth/login/', LoginAPI.as_view(), name='auth_login'),
    path('api/auth/logout/', LogoutAPI.as_view(), name='auth_logout'),
    # مسار الـ API الجديد الخاص بك
    path('api/gps/live/', LiveGPSLocationAPI.as_view(), name='live-gps-location'),
    path('api/gps/add/', AddGPSDeviceAPI.as_view(), name='add_gps_device'),
    path('api/gps/my-devices/', GetMyDevicesAPI.as_view(), name='get-my-devices'),
    path('api/gps/settings/all/', DeviceAllSettingsAPI.as_view(), name='device-all-settings-crud'),
    path('api/gps/emergency-numbers/', EmergencyNumbersAPI.as_view(), name='emergency-numbers-crud'),
    path('api/gps/oil/change/', OilChangeRecordAPI.as_view(), name='record-oil-change'),
    path('api/gps/oil/status/', OilStatusAPI.as_view(), name='get-oil-status'),

    
]

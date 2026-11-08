from django.contrib import admin
from django.urls import path
from gps_api.views import LiveGPSLocationAPI
from gps_api.views import AddGPSDeviceAPI,GetMyDevicesAPI,DeviceAllSettingsAPI,EmergencyNumbersAPI
from gps_api.views import OilChangeRecordAPI, OilStatusAPI,CreateOrUpdatePinCodeView ,VerifyPinCodeView
from gps_api.views import RegisterAPI, LoginAPI, LogoutAPI
from gps_api.views import  traccar_webhook ,GPSAlarmLogListView,GPSAlarmLogDeviceListView
from gps_api.views import SyncTraccarTripsView, GPSTripListView,get_full_battery_status,get_device_battery_status,safe_engine_stop
from gps_api.views_sms import ToggleMicView,StopEngineView,SetSosNumbersView,GetSosNumbersView
from gps_api.views import GPSDeviceDetailAPIView,DeleteGPSAlarmLogAPIView
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/register/', RegisterAPI.as_view(), name='auth_register'),
    path('api/auth/login/', LoginAPI.as_view(), name='auth_login'),
    path('api/auth/logout/', LogoutAPI.as_view(), name='auth_logout'),
    # مسار الـ API الجديد الخاص بك
    path('api/gps/live/', LiveGPSLocationAPI.as_view(), name='live-gps-location'),
    path('api/gps/add/', AddGPSDeviceAPI.as_view(), name='add_gps_device'),
     path('api/gps/devices/<int:pk>/', GPSDeviceDetailAPIView.as_view(), name='device-detail'),
    path('api/gps/my-devices/', GetMyDevicesAPI.as_view(), name='get-my-devices'),
    path('api/gps/settings/all/', DeviceAllSettingsAPI.as_view(), name='device-all-settings-crud'),
    path('api/gps/emergency-numbers/', EmergencyNumbersAPI.as_view(), name='emergency-numbers-crud'),
    path('api/gps/oil/change/', OilChangeRecordAPI.as_view(), name='record-oil-change'),
    path('api/gps/oil/status/', OilStatusAPI.as_view(), name='get-oil-status'),

    path('api/traccar/webhook/',traccar_webhook, name='traccar_webhook'),
    
    path('api/gps/<int:pk>/stope-engine/', StopEngineView.as_view(), name='toggle-mic'),
    path('api/gps/<int:pk>/toggle-mic/', ToggleMicView.as_view(), name='toggle-mic'),
    path('api/gps/<int:pk>/set-sos/', SetSosNumbersView.as_view(), name='set-sos'),
    path('api/gps/<int:pk>/get-sos/', GetSosNumbersView.as_view(), name='get-sos'),

    path('api/gps/alarm-logs/', GPSAlarmLogListView.as_view(), name='gps-alarm-logs'),

    path('api/gps/alarm-logs/<int:pk>/delete/', DeleteGPSAlarmLogAPIView.as_view(), name='delete-alarm-log'),

     path('api/gps/devices/<int:device_id>/logs/', GPSAlarmLogDeviceListView.as_view(), name='gps-device-alarm-logs'),
    path('api/gps/<int:pk>/set-pin/', CreateOrUpdatePinCodeView.as_view(), name='set-gps-pin'),
    path('api/gps/<int:pk>/verify-pin/', VerifyPinCodeView.as_view(), name='verify-gps-pin'),

# رابط المزامنة والسحب التلقائي (POST)
    path('api/gps/devices/<int:device_id>/trips/sync/', SyncTraccarTripsView.as_view(), name='sync-traccar-trips'),
    
    # رابط العرض العادي للسجلات من قاعدة بياناتك (GET)
    path('api/gps/devices/<int:device_id>/trips/', GPSTripListView.as_view(), name='gps-device-trips'),
    path('api/device/<int:device_id>/battery/', get_device_battery_status, name='device_battery'),
    path('api/device/<int:device_id>/full_battery/', get_full_battery_status, name='device_full_battery'),
   
  ]    

    #Token bf1a6f59a1d9b3a2a09b887668c05e9e5aca88bf
#   key     AIzaSyDCdEIDkw__SjwIdfaChCbhCCG_AA-YmQc
# git status
#/  git add gps_project/settings.py gps_project/urls.py gps_api/views.py gps_api/views_sms.py gps_api/utils.py
# git commit -m " webhook"
#git push origin main

#on Aws
#git pull origin main
#sudo systemctl restart gunicorn
#sudo systemctl restart nginx


#add limted device 
# add fcm 




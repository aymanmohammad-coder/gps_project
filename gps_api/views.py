import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.contrib.auth.models import User
from rest_framework.permissions import IsAuthenticated # اختياري: لحظر المستخدمين غير المسجلين
from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from requests.auth import HTTPBasicAuth
from .models import UserProfile,GPSDevice, DeviceOdometer,SpeedSettings, VibrationSettings, GeofenceSettings,EmergencyNumbers ,OilChangeSettings, OilChangeLog,UserDeviceToken,GPSAlarmLog
from rest_framework import serializers
from rest_framework.generics import DestroyAPIView
from rest_framework.authentication import TokenAuthentication
from rest_framework.pagination import PageNumberPagination
from django.contrib.auth import authenticate
from .models import UserProfile, UserDeviceToken
from .utils import send_textbee_sms

from rest_framework.generics import RetrieveUpdateDestroyAPIView
from .serializers import GPSDeviceSerializer,GPSAlarmLogSerializer
from .permissions import IsDeviceOwner,IsAlarmLogOwner
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from .models import VehicleLicense, GPSDevice
from .serializers import VehicleLicenseSerializer




class LiveGPSLocationAPI(APIView):
    """
    API مطور لجلب كافة ميزات وبيانات جهاز الـ GPS من نظام Traccar بالكامل
    مع استخراج متقدم لحالة الحساسات والتنبيهات
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request):
        device_id = request.query_params.get('device_id')
        
        if not device_id:
            return Response({"error": "يرجى تزويدنا بـ device_id الخاص بالجهاز"}, status=status.HTTP_400_BAD_REQUEST)
        
        positions_url = f"{settings.TRACCAR_SERVER_URL}api/positions"
        devices_url = f"{settings.TRACCAR_SERVER_URL}api/devices"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
        
        try:
            # 1. جلب بيانات الجهاز الأساسية
            dev_response = requests.get(devices_url, auth=auth_credentials, params={'id': device_id}, timeout=5.0)
            
            if dev_response.status_code != 200:
                return Response({
                    "error": "فشل جلب بيانات الجهاز من خادم Traccar",
                    "status_code": dev_response.status_code
                }, status=status.HTTP_502_BAD_GATEWAY)
                
            dev_json = dev_response.json()
            if not dev_json:
                return Response({"message": "لم يتم العثور على جهاز بهذا المعرف الداخلي (ID)"}, status=status.HTTP_404_NOT_FOUND)
                
            # بما أن الاستجابة مصفوفة، نأخذ العنصر الأول إذا كانت قائمة أو نأخذ القاموس مباشرة
            device_info = dev_json[0] if isinstance(dev_json, list) else dev_json
            
            # 2. طلب آخر موقع مسجل للجهاز لجلب الحساسات
            params = {'deviceId': device_id}
            pos_response = requests.get(positions_url, auth=auth_credentials, params=params, timeout=5.0)
            
            positions_data = []
            if pos_response.status_code == 200:
                positions_data = pos_response.json()
            
            latest_position_raw = positions_data[-1] if positions_data else {}
            
            # 3. استخراج الحساسات المتقدمة من حقل attributes
            attributes = latest_position_raw.get("attributes", {})
            
            # قراءة الحساسات الحية الخاصة بـ Coban 303
            engine_ignition = attributes.get("ignition", False)
            door_status = attributes.get("door", False)
            fuel_level = attributes.get("fuel", 0.0)
            active_alarm = attributes.get("alarm", None) # يرجع نوع التنبيه مثل (sos, lowBattery, etc.) إن وجد
            motion_status = attributes.get("motion", False)
            
            # بناء استجابة ذكية ومنظمة تحتوي على الحقول الإضافية مستخرجة جاهزة للفرونت إند
            full_features_response = {
                "device_summary": {
                    "id": device_info.get("id"),
                    "name": device_info.get("name"),
                    "unique_id_imei": device_info.get("uniqueId"),
                    "status": device_info.get("status"),              # online / offline
                    "last_update": device_info.get("lastUpdate"),
                    "model": device_info.get("model"),
                    "phone_number": device_info.get("phone"),
                    "category": device_info.get("category"),
                },
                "live_sensors": {
                    "is_engine_on": engine_ignition,                  # هل السيارة تعمل؟
                    "is_door_open": door_status,                      # هل الباب مفتوح؟
                    "fuel_level_liters": fuel_level,                  # مستوى الوقود
                    "current_speed_kph": round(latest_position_raw.get("speed", 0) * 1.852, 2) if latest_position_raw else 0, # تحويل السرعة من عقدة إلى كم/ساعة
                    "is_moving": motion_status,                        # هل السيارة تتحرك حالياً؟
                    "active_alarm": active_alarm,                     # التنبيه الحالي (إن وجد)
                    "has_alarm": True if active_alarm else False       # نعم/لا لوجود تنبيه خطر
                },
                "location": {
                    "latitude": latest_position_raw.get("latitude"),
                    "longitude": latest_position_raw.get("longitude"),
                    "altitude": latest_position_raw.get("altitude"),
                },
                "gps_raw_data": latest_position_raw, 
            }
            
            return Response(full_features_response, status=status.HTTP_200_OK)
                
        except requests.exceptions.RequestException as e:
            return Response({"error": f"تعذر الاتصال بخادم Traccar الخارجي: {str(e)}"}, status=status.HTTP_504_GATEWAY_TIMEOUT)


import threading
import time
import requests
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from .utils import dispatch_device_command


def run_initial_device_setup(device):
    """دالة تعمل في الخلفية لإرسال رسائل التهيئة وتفعيل الاهتزاز تلقائياً دون تأخير رد الـ API"""
    if not device.phone_number:
        return

    server_ip = getattr(settings, 'TRACCAR_SERVER_IP', 'YOUR_SERVER_IP')
    
    # تحديد الـ APN تلقائياً حسب شبكة الشريحة السورية
    clean_phone = str(device.phone_number).replace("+963", "0").strip()
    if clean_phone.startswith(("094", "095", "096")):
        apn = "internet"          # MTN Syria
    else:
        apn = "://syriatel.com"  # Syriatel

    if device.model == "Copon_303F":
        commands_sequence = [
            ("init_begin", {}),
            ("init_timezone", {}),
            ("init_apn", {"apn": apn}),
            ("init_ip", {"ip": server_ip, "port": 5001}),
            ("init_gprs", {}),
            ("init_fix", {}),
            ("shock_on", {}),  # <-- تفعيل تنبيه الاهتزاز تلقائياً
            ("acc_on", {}),    # <-- تفعيل تنبيه تشغيل/إطفاء المحرك
        ]
    elif device.model == "Istartek_VT200_L":
        commands_sequence = [
            ("init_apn", {"apn": apn}),
            ("init_ip", {"ip": server_ip, "port": 5222}),
            ("init_interval", {}),
            ("init_timezone", {}),
            ("shock_on", {}),  # <-- تفعيل تنبيه الاهتزاز تلقائياً
            ("acc_on", {}),    # <-- تفعيل إرسال أحداث المحرك للسيرفر

        ]
    else:
        return

    for cmd_key, params in commands_sequence:
        dispatch_device_command(device, cmd_key, **params)
        time.sleep(2.5)

class AddGPSDeviceAPI(APIView):
    """
    API لإضافة جهاز جديد إلى خادم Traccar وقاعدة بيانات دجانغو وإرسال رسائل التهيئة تلقائياً
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = request.data.get('name')
        imei = request.data.get('imei')
        model = request.data.get('model')
        phone = request.data.get('phone_number', None)
        category = request.data.get('category', 'car')

        if not name or not imei:
            return Response({"error": "الحقول 'name' و 'imei' مطلوبة بشكل إجباري"}, status=status.HTTP_400_BAD_REQUEST)

        target_user = request.user

        if GPSDevice.objects.filter(imei=imei).exists():
            return Response({"error": "هذا الجهاز (IMEI) مضاف بالفعل في النظام المحلي"}, status=status.HTTP_400_BAD_REQUEST)

        expiration_date = timezone.now() + timedelta(days=180)
        base_url = settings.TRACCAR_SERVER_URL
        if not base_url.endswith('/'):
            base_url += '/'
        devices_url = f"{base_url}api/devices"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)

        traccar_payload = {
            "name": name,
            "uniqueId": imei,
            "model": model,
            "phone": phone,
            "category": category,
            "expirationTime": expiration_date.isoformat()
        }

        try:
            traccar_response = requests.post(
                devices_url,
                auth=auth_credentials,
                json=traccar_payload,
                timeout=5.0
            )

            if traccar_response.status_code in[200,201]:
                traccar_data = traccar_response.json()
                assigned_traccar_id = traccar_data.get('id')

                if GPSDevice.objects.filter(id=assigned_traccar_id).exists():
                    return Response({"error": f"معرف الجهاز (ID: {assigned_traccar_id}) محجوز مسبقاً في النظام المحلي"}, status=status.HTTP_409_CONFLICT)

                # 1. حفظ الجهاز محلياً
                local_device = GPSDevice.objects.create(
                    id=assigned_traccar_id,
                    user=target_user,
                    name=name,
                    imei=imei,
                    traccar_id=assigned_traccar_id,
                    model=model,
                    phone_number=phone,
                    expiration_date=expiration_date
                )

                # 2. إنشاء السجلات الافتراضية للإعدادات في قاعدة البيانات
                SpeedSettings.objects.get_or_create(device=local_device, defaults={'max_speed': 120.0, 'is_active': True})
                VibrationSettings.objects.get_or_create(device=local_device, defaults={'max_vibration': 5, 'is_active': True})
                GeofenceSettings.objects.get_or_create(device=local_device, defaults={'is_active': True})

                # 3. بدء إرسال رسائل ربط الـ IP والتهيئة في الخلفية (إذا كان رقم الشريحة مدخلاً)
                sms_started = False
                if phone and model in ["Copon_303F", "Istartek_VT200_L"]:
                    threading.Thread(target=run_initial_device_setup, args=(local_device,), daemon=True).start()
                    sms_started = True

                return Response({
                    "message": "تم إضافة الجهاز بنجاح وبدأ إرسال رسائل التهيئة وربط السيرفر تلقائياً",
                    "sms_setup_triggered": sms_started,
                    "user_associated": {
                        "user_id": target_user.id,
                        "username": target_user.username
                    },
                    "local_id": local_device.id,
                    "traccar_id": assigned_traccar_id,
                    "expiration_date": local_device.expiration_date.strftime('%Y-%m-%d %H:%M:%S'),
                    "device_details": traccar_data
                }, status=status.HTTP_201_CREATED)

            else:
                return Response({
                    "error": "فشل إضافة الجهاز في خادم Traccar الخارجي",
                    "status_code": traccar_response.status_code,
                    "traccar_error": traccar_response.text
                }, status=status.HTTP_502_BAD_GATEWAY)

        except requests.exceptions.RequestException as e:
            return Response({
                "error": f"تعذر الاتصال بخادم Traccar الخارجي لإتمام الإضافة: {str(e)}"
            }, status=status.HTTP_504_GATEWAY_TIMEOUT)

class AddGPSDeviceAPI____a(APIView):
    """
    API لإضافة جهاز جديد إلى خادم Traccar وقاعدة بيانات دجانغو المحلية معاً بالاعتماد على Token المستخدم
    """
    # 1. إجبار الـ API على قراءة الـ Token والتحقق من هوية المرسل
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request):
        name = request.data.get('name')
        imei = request.data.get('imei') # الـ Unique ID الخاص بالجهاز
        model = request.data.get('model') 
        phone = request.data.get('phone_number', None)
        category = request.data.get('category', 'car')

        # 2. التحقق من المدخلات الأساسية
        if not name or not imei:
            return Response({"error": "الحقول 'name' و 'imei' مطلوبة بشكل إجباري"}, status=status.HTTP_400_BAD_REQUEST)

        # 3. جلب المستخدم تلقائياً بناءً على الـ Token الممرر بالطلب
        target_user = request.user

        # التحقق من عدم تكرار الجهاز محلياً قبل إرساله للسيرفر
        if GPSDevice.objects.filter(imei=imei).exists():
            return Response({"error": "هذا الجهاز (IMEI) مضاف بالفعل في النظام المحلي"}, status=status.HTTP_400_BAD_REQUEST)
        expiration_date = timezone.now() + timedelta(days=180)
        # 4. تجهيز البيانات والإعدادات لخادم Traccar
        base_url = settings.TRACCAR_SERVER_URL
        if not base_url.endswith('/'):
            base_url += '/'
        devices_url = f"{base_url}api/devices"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)

        # هيكل البيانات الذي يتوقعه Traccar API لإنشاء جهاز
        traccar_payload = {
            "name": name,
            "uniqueId": imei,
            "model": model,
            "phone": phone,
            "category": category,
             "expirationTime": expiration_date.isoformat()  # يدعمه سيرفر Traccar تلقائياً
      
        }

        try:
            # إرسال طلب POST لخادم Traccar لإنشاء الجهاز
            traccar_response = requests.post(
                devices_url,
                auth=auth_credentials,
                json=traccar_payload,
                timeout=5.0
            )

            # تم تصحيح الشرط هنا للتحقق من كود 200 أو 201 الناجح
            if traccar_response.status_code ==200 : 
                traccar_data = traccar_response.json()
                assigned_traccar_id = traccar_data.get('id')  # الحصول على الـ ID الداخلي من Traccar

                # للتأكد من عدم وجود جهاز محلي يحمل نفس الـ ID مسبقاً (تجنباً لـ IntegrityError)
                if GPSDevice.objects.filter(id=assigned_traccar_id).exists():
                    return Response({"error": f"معرف الجهاز (ID: {assigned_traccar_id}) محجوز مسبقاً في النظام المحلي"}, status=status.HTTP_409_CONFLICT)

                # 5. حفظ الجهاز في قاعدة بيانات دجانغو وتمرير traccar_id ليكون هو الـ id الأساسي
                local_device = GPSDevice.objects.create(
                    id=assigned_traccar_id,  # جعل رقم الجهاز المحلي هو نفسه traccar_id
                    user=target_user,
                    name=name,
                    imei=imei,
                    traccar_id=assigned_traccar_id,
                    model=model,
                    phone_number=phone,
                    expiration_date=expiration_date
                )

                # إرجاع رد نجاح العملية يحتوي على بيانات كِلا الطرفين مع بيانات المستخدم المرتبط
                return Response({
                    "message": "تم إضافة الجهاز بنجاح وربطه بحسابك الشخصي عبر الـ Token المرفق",
                    "user_associated": {
                        "user_id": target_user.id,
                        "username": target_user.username
                    },
                    "local_id": local_device.id,  # سيكون مطابقاً لـ traccar_id تماماً
                    "traccar_id": assigned_traccar_id,
                    "expiration_date": local_device.expiration_date.strftime('%Y-%m-%d %H:%M:%S'),
                    
                    "device_details": traccar_data
                }, status=status.HTTP_201_CREATED)
                
            else:
                # في حال رفض Traccar إضافة الجهاز
                return Response({
                    "error": "فشل إضافة الجهاز في خادم Traccar الخارجي",
                    "status_code": traccar_response.status_code,
                    "traccar_error": traccar_response.text
                }, status=status.HTTP_502_BAD_GATEWAY)

        except requests.exceptions.RequestException as e:
            return Response({
                "error": f"تعذر الاتصال بخادم Traccar الخارجي لإتمام الإضافة: {str(e)}"
            }, status=status.HTTP_504_GATEWAY_TIMEOUT)



class GPSDeviceDetailAPIView(RetrieveUpdateDestroyAPIView):
    """
    API لجلب تفاصيل، تحديث، وحذف جهاز الـ GPS 
    بشكل متزامن بين قاعدة بيانات Django وسيرفر Traccar.
    """
    queryset = GPSDevice.objects.all()
    serializer_class = GPSDeviceSerializer
    
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsDeviceOwner]

    def get_queryset(self):
        return GPSDevice.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        """
        تخصيص عملية التحديث لتحديث البيانات في Traccar أولاً ثم في Django
        """
        # جلب الكائن الحالي قبل الحفظ لمعرفة القيم الثابتة الأصلية
        current_device = self.get_object()
        
        # حفظ التعديلات الجديدة محلياً في ذاكرة الكائن (وليس في قاعدة البيانات بعد)
        # لتمريرها إلى سيرفر Traccar أولاً
        instance = serializer.save()

        base_url = settings.TRACCAR_SERVER_URL
        if not base_url.endswith('/'):
            base_url += '/'
            
        update_url = f"{base_url}api/devices/{current_device.traccar_id}"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)

        # بناء البيانات المرسلة لـ Traccar مع الحفاظ على imei و traccar_id الأصليين
        traccar_payload = {
            "id": current_device.traccar_id,
            "name": instance.name,
            "uniqueId": current_device.imei,  # استخدام الـ imei الثابت من قاعدة البيانات
            "model": instance.model,
            "phone": instance.phone_number,
            "expirationTime": instance.expiration_date.isoformat() if instance.expiration_date else None
        }

        try:
            # إرسال طلب التحديث للسيرفر الخارجي
            traccar_response = requests.put(
                update_url,
                auth=auth_credentials,
                json=traccar_payload,
                timeout=5.0
            )

            # إذا رفض السيرفر التعديل، نرفع خطأ لإلغاء الحفظ
            if traccar_response.status_code != 200:
                raise requests.exceptions.RequestException(
                    f"Traccar error {traccar_response.status_code}: {traccar_response.text}"
                )

        except requests.exceptions.RequestException as e:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                "error": f"فشل تحديث البيانات في خادم Traccar الخارجي، تم إلغاء التعديل المحلي: {str(e)}"
            })

    def destroy(self, request, *args, **kwargs):
        """
        تخصيص عملية الحذف لحذف الجهاز من سيرفر Traccar أولاً ثم من Django
        """
        device = self.get_object()

        base_url = settings.TRACCAR_SERVER_URL
        if not base_url.endswith('/'):
            base_url += '/'
            
        delete_url = f"{base_url}api/devices/{device.traccar_id}"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)

        try:
            traccar_response = requests.delete(
                delete_url,
                auth=auth_credentials,
                timeout=5.0
            )

            if traccar_response.status_code in [200, 204, 404]:
                device_id = device.id
                device.delete()
                
                return Response({
                    "message": f"تم حذف الجهاز ذو المعرف {device_id} بنجاح من سيرفر Traccar ومن النظام المحلي."
                }, status=status.HTTP_200_OK)
                
            else:
                return Response({
                    "error": "فشل حذف الجهاز من خادم Traccar الخارجي، تم إلغاء عملية الحذف المحلية",
                    "status_code": traccar_response.status_code,
                    "traccar_error": traccar_response.text
                }, status=status.HTTP_502_BAD_GATEWAY)

        except requests.exceptions.RequestException as e:
            return Response({
                "error": f"تعذر الاتصال بخادم Traccar لإتمام الحذف: {str(e)}"
            }, status=status.HTTP_504_GATEWAY_TIMEOUT)


class GetMyDevicesAPI(APIView):
    """
    API لجلب كافة الأجهزة التابعة للمستخدم الحالي والمسجلة باسمه عبر الـ Token
    """
    # إجبار الـ API على قراءة الـ Token والتحقق من هوية المستخدم
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. جلب المستخدم الحالي تلقائياً بناءً على الـ Token المرسل بالطلب
        current_user = request.user
        
        # 2. جلب كافة أجهزة الـ GPS التابعة لهذا المستخدم فقط من قاعدة البيانات
        user_devices = GPSDevice.objects.filter(user=current_user).order_by('-created_at')
        
        # التحقق في حال لم يقم المستخدم بإضافة أي جهاز بعد
        if not user_devices.exists():
            return Response({
                "message": "لم يتم العثور على أي أجهزة مسجلة تحت حسابك حالياً.",
                "total_devices": 0,
                "devices": []
            }, status=status.HTTP_200_OK)
            
        # 3. بناء مصفوفة البيانات (Serialize) لتجهيز الرد بصيغة JSON نظيفة
        devices_list = []
        for device in user_devices:
            devices_list.append({
                "local_id": device.id,
                "name": device.name,
                "imei": device.imei,
                "traccar_id": device.traccar_id,
                "model": device.model,
                "phone_number": device.phone_number,
                "expiration_date":device.expiration_date,
                "days_remaining": device.days_left,
                "created_at": device.created_at.strftime('%Y-%m-%d %H:%M:%S')  # تنسيق الوقت المقروء
            })
            
        # 4. إرجاع النتيجة الناجحة للمستخدم
        return Response({
            "message": "تم جلب أجهزتك بنجاح",
            "total_devices": user_devices.count(),
            "devices": devices_list
        }, status=status.HTTP_200_OK)



import random
class RegisterAPI(APIView):
    """API لتسجيل مستخدم جديد وإرسال كود التفعيل عبر SMS"""
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        email = request.data.get('email', '')
        mobile_num = request.data.get('mobile_num')

        # 1. التحقق من الحقول الإجبارية
        if not username or not password or not mobile_num:
            return Response(
                {'error': 'اسم المستخدم، كلمة المرور، ورقم الهاتف حقول إجبارية'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. التحقق من عدم تكرار اسم المستخدم أو رقم الهاتف
        if User.objects.filter(username=username).exists():
            return Response({'error': 'اسم المستخدم هذا مأخوذ بالفعل'}, status=status.HTTP_400_BAD_REQUEST)

        if UserProfile.objects.filter(mobile_num=mobile_num).exists():
            return Response({'error': 'رقم الهاتف هذا مسجل مسبقاً'}, status=status.HTTP_400_BAD_REQUEST)

        # توليد كود عشوائي من 6 أرقام
        otp_code = str(random.randint(100000, 999999))
        sms_message = f"كود تفعيل حسابك هو: {otp_code}"

        # استخدام transaction لضمان عدم إنشاء حساب في حال فشل إرسال الرسالة
        try:
            with transaction.atomic():
                # إنشاء المستخدم بحالة غير مفعل (is_active=False)
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=email,
                    is_active=False
                )

                # حفظ رقم الهاتف والكود في البروفايل
                UserProfile.objects.create(
                    user=user,
                    mobile_num=mobile_num,
                    otp_code=otp_code,
                    otp_created_at=timezone.now()
                )

                # إرسال الرسالة عبر Textbee (يجب تمرير الرقم داخل قائمة)
                success, sms_response = send_textbee_sms([mobile_num], sms_message)
                
                if not success:
                    # رفع خطأ لإلغاء حفظ المستخدم في قاعدة البيانات
                    raise Exception(sms_response)

        except Exception as e:
            return Response(
                {'error': f'فشل إرسال رسالة التفعيل: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'message': 'تم إنشاء الحساب بنجاح. يرجى إدخال كود التحقق المرسل إلى هاتفك لتفعيل الحساب.',
            'username': user.username,
            'mobile_num': mobile_num
        }, status=status.HTTP_201_CREATED)


class UserProfileAPI(APIView):
    """
    API لجلب بيانات الملف الشخصي للمستخدم المسجل حالياً
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # جلب البروفايل بأمان دون حدوث خطأ إذا لم يكن منشأً بعد
        profile = UserProfile.objects.filter(user=user).first()

        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "mobile_num": profile.mobile_num if profile else None,
            "date_joined": user.date_joined.strftime('%Y-%m-%d %H:%M:%S') if user.date_joined else None,
            "devices_count": user.gps_devices.count()  # معلومة إضافية مفيدة لواجهة البروفايل
        }, status=status.HTTP_200_OK)

class VerifyOTPAPI(APIView):
    """API للتحقق من كود SMS وتفعيل الحساب"""
    def post(self, request):
        mobile_num = request.data.get('mobile_num')
        otp_code = request.data.get('otp_code')

        if not mobile_num or not otp_code:
            return Response(
                {'error': 'يرجى إدخال رقم الهاتف وكود التحقق'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            profile = UserProfile.objects.select_related('user').get(mobile_num=mobile_num)
        except UserProfile.DoesNotExist:
            return Response({'error': 'رقم الهاتف غير مسجل في النظام'}, status=status.HTTP_404_NOT_FOUND)

        user = profile.user

        if user.is_active:
            return Response({'message': 'الحساب مفعل مسبقاً'}, status=status.HTTP_200_OK)

        # التحقق من تطابق الكود وصلاحيته
        if profile.otp_code != str(otp_code):
            return Response({'error': 'كود التحقق غير صحيح'}, status=status.HTTP_400_BAD_REQUEST)

        if not profile.is_otp_valid():
            return Response({'error': 'انتهت صلاحية كود التحقق، يرجى طلب كود جديد'}, status=status.HTTP_400_BAD_REQUEST)

        # تفعيل الحساب وتصفير الكود
        user.is_active = True
        user.save()

        profile.otp_code = None
        profile.save()

        # إنشاء التوكين الآن بعد التفعيل ليدخل المستخدم مباشرة
        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            'message': 'تم تفعيل الحساب بنجاح',
            'uid': user.id,
            'token': token.key,
            'username': user.username
        }, status=status.HTTP_200_OK)


class LoginAPI(APIView):
    """API لتسجيل الدخول وإنشاء مفتاح التحقق (Token)"""
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        fcm_token = request.data.get('fcm_token')

        if not username or not password:
            return Response({'error': 'يرجى إدخال اسم المستخدم وكلمة المرور'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_obj = User.objects.get(username=username)
            if not user_obj.is_active:
                return Response(
                    {'error': 'حسابك غير مفعل. يرجى تأكيد رقم هاتفك أولاً.', 'is_verified': False},
                    status=status.HTTP_403_FORBIDDEN
                )
        except User.DoesNotExist:
            pass

        user = authenticate(username=username, password=password)

        if user is not None:
            token, _ = Token.objects.get_or_create(user=user)
            if fcm_token:
                UserDeviceToken.objects.update_or_create(
                    fcm_token=fcm_token,
                    defaults={'user': user}
                )
            return Response({
                'message': 'تم تسجيل الدخول بنجاح',
                'uid': user.id,
                'token': token.key,
                'username': user.username
            }, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'بيانات الدخول غير صحيحة، يرجى المحاولة مجدداً'}, status=status.HTTP_401_UNAUTHORIZED)
class ForgetPasswordRequestAPI(APIView):
    """API لطلب استعادة كلمة المرور وإرسال كود التحقق عبر SMS"""
    def post(self, request):
        username = request.data.get('username')
        mobile_num = request.data.get('mobile_num')

        if not username or not mobile_num:
            return Response(
                {'error': 'يرجى إدخال اسم المستخدم ورقم الجوال'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # التحقق من تطابق اسم المستخدم مع رقم الجوال في النظام
        try:
            profile = UserProfile.objects.select_related('user').get(
                user__username=username,
                mobile_num=mobile_num
            )
        except UserProfile.DoesNotExist:
            return Response(
                {'error': 'اسم المستخدم أو رقم الجوال غير صحيح، أو غير متطابقين'},
                status=status.HTTP_404_NOT_FOUND
            )

        # توليد كود عشوائي من 6 أرقام
        otp_code = str(random.randint(100000, 999999))
        sms_message = f"كود استعادة كلمة المرور الخاصة بحسابك هو: {otp_code}"

        # حفظ الكود ووقت الإنشاء في البروفايل
        profile.otp_code = otp_code
        profile.otp_created_at = timezone.now()
        profile.save()

        # إرسال الرسالة عبر Textbee
        success, sms_response = send_textbee_sms([mobile_num], sms_message)

        if not success:
            return Response(
                {'error': f'فشل إرسال رسالة التحقق: {sms_response}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'message': 'تم إرسال كود التحقق إلى رقم جوالك بنجاح',
            'username': username,
            'mobile_num': mobile_num
        }, status=status.HTTP_200_OK)


class ResetPasswordConfirmAPI(APIView):
    """API للتحقق من الكود وتعيين كلمة المرور الجديدة"""
    def post(self, request):
        username = request.data.get('username')
        mobile_num = request.data.get('mobile_num')
        otp_code = request.data.get('otp_code')
        new_password = request.data.get('new_password')

        if not all([username, mobile_num, otp_code, new_password]):
            return Response(
                {'error': 'جميع الحقول مطلوبة (اسم المستخدم، رقم الجوال، الكود، كلمة المرور الجديدة)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # جلب البروفايل والمستخدم
        try:
            profile = UserProfile.objects.select_related('user').get(
                user__username=username,
                mobile_num=mobile_num
            )
        except UserProfile.DoesNotExist:
            return Response(
                {'error': 'بيانات الحساب غير صحيحة'},
                status=status.HTTP_404_NOT_FOUND
            )

        # التحقق من صحة الكود وصلاحيته
        if not profile.otp_code or profile.otp_code != str(otp_code):
            return Response(
                {'error': 'كود التحقق غير صحيح'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not profile.is_otp_valid():
            return Response(
                {'error': 'انتهت صلاحية كود التحقق، يرجى طلب كود جديد'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = profile.user

        # تعيين كلمة المرور الجديدة وتشفيرها تلقائياً
        user.set_password(new_password)
        
        # في حال كان الحساب غير مفعل سابقاً، يمكن تفعيله هنا تلقائياً لأنه أثبت ملكية الرقم
        if not user.is_active:
            user.is_active = True
            
        user.save()

        # تصفير الكود لمنع إعادة استخدامه
        profile.otp_code = None
        profile.save()

        # حذف التوكين القديم وإصدار توكين جديد لزيادة الأمان (إخراج أي جلسات قديمة)
        Token.objects.filter(user=user).delete()
        new_token = Token.objects.create(user=user)

        return Response({
            'message': 'تم تغيير كلمة المرور بنجاح',
            'token': new_token.key,
            'username': user.username
        }, status=status.HTTP_200_OK)

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError



class ChangePasswordAPI(APIView):
    """API لتغيير كلمة المرور للمستخدم المسجل"""

    # يجب أن يكون المستخدم مسجلاً للدخول (يرسل التوكين في الهيدر)
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get('old_password')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')

        # 1. التحقق من إدخال جميع الحقول المطلوبة
        if not old_password or not new_password or not confirm_password:
            return Response(
                {'error': 'جميع الحقول إجبارية (القديمة، الجديدة، وتأكيد الجديدة)'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. التحقق من صحة كلمة المرور القديمة
        if not user.check_password(old_password):
            return Response(
                {'error': 'كلمة المرور القديمة غير صحيحة'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. التحقق من تطابق كلمة المرور الجديدة مع تأكيدها
        if new_password != confirm_password:
            return Response(
                {'error': 'كلمة المرور الجديدة وتأكيدها غير متطابقتين'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. (اختياري وموصى به) التحقق من قوة كلمة المرور حسب إعدادات Django
        try:
            validate_password(new_password, user=user)
        except ValidationError as e:
            return Response(
                {'error': list(e.messages)}, status=status.HTTP_400_BAD_REQUEST
            )

        # 5. حفظ وتشفير كلمة المرور الجديدة
        user.set_password(new_password)
        user.save()

        return Response(
            {'message': 'تم تغيير كلمة المرور بنجاح'}, status=status.HTTP_200_OK
        )

class LogoutAPI(APIView):
    """
    API لتسجيل الخروج وحذف مفتاح التحقق (Token) لضمان الأمان
    """
    # حماية هذا المنفذ بحيث لا يمكن استدعاؤه إلا من مستخدم قام بتسجيل الدخول بالفعل
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            # حذف التوكين الحالي للمستخدم من قاعدة البيانات لمنع استخدامه مجدداً
            request.user.auth_token.delete()
            return Response({"message": "تم تسجيل الخروج وحذف التوكين بنجاح"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": f"حدث خطأ أثناء تسجيل الخروج: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




class VehicleLicenseAPI(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    # 1. جلب بيانات الرخصة والأيام المتبقية (GET)
    def get(self, request):
        device_pk = request.query_params.get('device_id')
        if not device_pk:
            return Response(
                {"error": "يرجى تزويدنا بمعرف الجهاز (device)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        license_obj = get_object_or_404(VehicleLicense, device_id=device_pk)
        serializer = VehicleLicenseSerializer(license_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # 2. إضافة بيانات الرخصة لجهاز (POST)
    def post(self, request):
        serializer = VehicleLicenseSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "message": "تم إضافة بيانات الرخصة بنجاح",
                    "data": serializer.data
                },
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # 3. تعديل تاريخ الرخصة أو رقم اللوحة (PATCH)
    def patch(self, request):
        device_pk = request.data.get('device_id') or request.query_params.get('device_id')
        if not device_pk:
            return Response(
                {"error": "يرجى إرسال معرف الجهاز (device) لتحديد الرخصة المراد تعديلها"},
                status=status.HTTP_400_BAD_REQUEST
            )

        license_obj = get_object_or_404(VehicleLicense, device_id=device_pk)
        serializer = VehicleLicenseSerializer(license_obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "message": "تم تحديث بيانات الرخصة بنجاح",
                    "data": serializer.data
                },
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OilChangeRecordAPI(APIView):
    """
    API لتسجيل عملية تغيير زيت جديدة وتحديث عداد الصيانة الافتراضي للجهاز
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device_id = request.data.get('device_id')
        oil_type = request.data.get('oil_type')
        filter_raw = request.data.get('filter_changed', False)
        filter_changed = str(filter_raw).lower() in ['true', '1', 'yes']
        oil_interval_km = float(request.data.get('oil_interval_km', 5000.0)) # المسافة الدورية الافتراضية
        warn_before_km = float(request.data.get('warn_before_km', 500.0))

        if not device_id or not oil_type:
            return Response({"error": "الحقول 'device_id' و 'oil_type' مطلوبة"}, status=status.HTTP_400_BAD_REQUEST)

        # التحقق من ملكية الجهاز
        try:
            device = GPSDevice.objects.get(user=request.user, id=device_id)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        # 1. جلب قراءة العداد الكلية الحالية (Total Distance) من سيرفر Traccar حياً لتسجيلها
        traccar_url = f"{settings.TRACCAR_SERVER_URL}api/positions"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
        
        try:
            response = requests.get(traccar_url, auth=auth_credentials, params={'deviceId': device.traccar_id}, timeout=5.0)
            current_odometer_km = 0.0
            if response.status_code == 200 and response.json():
                latest_position = response.json()[-1]
                # تراكار يحفظ المسافة بالأمتار، نقسم على 1000 لتحويلها إلى كيلومتر
                total_meters = latest_position.get('attributes', {}).get('totalDistance', 0.0)
                current_odometer_km = total_meters / 1000.0
            else:
                return Response({"error": "تعذر جلب قراءة عداد السيارة من سيرفر Traccar لتسجيل العملية","response":response.text}, status=status.HTTP_502_BAD_GATEWAY)
        except requests.exceptions.RequestException as e:
            return Response({"error": f"فشل الاتصال بسيرفر تراكار الداخلي: {str(e)}" }, status=status.HTTP_504_GATEWAY_TIMEOUT)

        # 2. توثيق وحفظ العملية في جدول السجل (Log)
        OilChangeLog.objects.create(
            device=device,
            oil_type=oil_type,
            filter_changed=filter_changed,
            current_odometer=current_odometer_km,
            notes=request.data.get('notes', '')
        )

        # 3. تحديث أو إنشاء إعدادات الزيت لتثبيت نقطة الصفر الجديدة للعداد
        oil_setting, _ = OilChangeSettings.objects.update_or_create(
            device=device,
            defaults={
                'oil_interval_km': oil_interval_km,
                'warn_before_km': warn_before_km,
                'last_change_odometer': current_odometer_km
            }
        )

        return Response({
            "message": "تم توثيق عملية تغيير الزيت بنجاح، وتصفير عداد الحساب الذكي",
            "recorded_odometer_km": round(current_odometer_km, 2),
            "next_change_at_km": round(current_odometer_km + oil_interval_km, 2)
        }, status=status.HTTP_201_CREATED)


class OilStatusAPI(APIView):
    """
    API ذكي لفحص حالة الزيت وحساب المسافة المتبقية وتحديد ما إذا كان يجب إطلاق تحذير مسبق
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            device = GPSDevice.objects.get(user=request.user, id=device_id)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك"}, status=status.HTTP_403_FORBIDDEN)

        # جلب إعدادات الصيانة للجهاز
        try:
            setting = device.oil_setting
        except OilChangeSettings.DoesNotExist:
            return Response({"message": "لم يتم تسجيل أي عملية تغيير زيت سابقة لهذا الجهاز حتى الآن"}, status=status.HTTP_200_OK)

        # جلب العداد الحالي حياً من تراكار
        traccar_url = f"{settings.TRACCAR_SERVER_URL}api/positions"
        auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
        
        try:
            response = requests.get(traccar_url, auth=auth_credentials, params={'deviceId': device.traccar_id}, timeout=5.0)
            if response.status_code == 200 and response.json():
                latest_position = response.json()[-1]
                current_odometer_km = latest_position.get('attributes', {}).get('totalDistance', 0.0) / 1000.0
            else:
                return Response({"error": "فشل استدعاء بيانات العداد الحية"}, status=status.HTTP_502_BAD_GATEWAY)
        except requests.exceptions.RequestException:
            return Response({"error": "خطأ في الاتصال بسيرفر تراكار"}, status=status.HTTP_504_GATEWAY_TIMEOUT)

        # الحسابات الرياضية البرمجية الذكية 🧠
        km_driven_since_change = current_odometer_km - setting.last_change_odometer
        km_remaining = setting.oil_interval_km - km_driven_since_change
        
        # تحديد حالة التحذير (إذا كانت المسافة المتبقية أقل من أو تساوي مسافة التحذير المحددة مثل 500 كم)
        needs_warning = km_remaining <= setting.warn_before_km
        is_overdue = km_remaining <= 0  # منتهى وتجاوز الحد تماماً

        status_text = "سليم"
        if is_overdue:
            status_text = "خطر! تم تجاوز مسافة صلاحية الزيت المحددة"
        elif needs_warning:
            status_text = f"تحذير: اقترب موعد تغيير الزيت (متبقي أقل من {setting.warn_before_km} كم)"

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "oil_status": status_text,
            "calculations": {
                "current_car_odometer_km": round(current_odometer_km, 1),
                "odometer_at_last_change_km": round(setting.last_change_odometer, 1),
                "kilometers_driven_on_current_oil": round(km_driven_since_change, 1),
                "kilometers_remaining_to_change": round(max(km_remaining, 0.0), 1),
                "target_change_at_odometer_km": round(setting.last_change_odometer + setting.oil_interval_km, 1)
            },
            "alerts": {
                "warning_zone_triggered": needs_warning,
                "overdue_triggered": is_overdue
            }
            
        }, status=status.HTTP_200_OK)
    

import os
import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import firebase_admin
from firebase_admin import credentials, messaging
from .models import GPSDevice


BASE_DIR = settings.BASE_DIR
cred_path = os.path.join(BASE_DIR, 'firebase-credentials.json')

# تهيئة Firebase Admin SDK إذا لم تكن مهيأة مسبقاً
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

###########################
import json
import math
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from firebase_admin import messaging
from .models import GPSDevice, SpeedSettings, VibrationSettings, GeofenceSettings, GPSAlarmLog, UserDeviceToken


def calculate_distance_meters(lat1, lon1, lat2, lon2):
    """حساب المسافة بالمتر بين نقطتين جغرافيتين (Haversine Formula)"""
    R = 6371000  # نصف قطر الأرض بالمتر
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

import json
from django.utils import timezone
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from firebase_admin import messaging



# استيراد الموديلات والدوال المساعدة من تطبيقك
from .models import (
    UserProfile,
)

class UpdateUserLanguageAPI(APIView):
    """
    API لحفظ وتحديث لغة التطبيق المفضلة للمستخدم في السيرفر (ar, en, tr)
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        lang = str(request.data.get('language', '')).strip().lower()
        if lang not in ['ar', 'en', 'tr']:
            return Response(
                {"error": "اللغة غير مدعومة. الخيارات المتاحة: ar, en, tr"},
                status=status.HTTP_400_BAD_REQUEST
            )

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.language = lang
        profile.save(update_fields=['language'])

        return Response({
            "success": True,
            "message": "تم تحديث لغة المستخدم بنجاح.",
            "language": profile.language
        }, status=status.HTTP_200_OK)


@csrf_exempt
def traccar_webhook(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'success', 'message': 'Webhook endpoint is active'}, status=200)

    if request.method == 'POST':
        try:
            if not request.body:
                return JsonResponse({'status': 'ignored', 'message': 'Empty body received'}, status=400)

            data = json.loads(request.body.decode('utf-8'))

            event = data.get('event') or {}
            position = data.get('position') or {}
            traccar_device_info = data.get('device') or {}

            event_type = event.get('type', '')
            pos_attrs = position.get('attributes') or {}
            evt_attrs = event.get('attributes') or {}
            attributes = {**pos_attrs, **evt_attrs}
            alarm_type = str(attributes.get('alarm', '')).lower()
            raw_evt_code = str(attributes.get('event', ''))

            traccar_id = event.get('deviceId') or traccar_device_info.get('id')
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)

            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)

            speed_setting = SpeedSettings.objects.filter(device=device).first()
            vibration_setting = VibrationSettings.objects.filter(device=device).first()
            geofence_setting = GeofenceSettings.objects.filter(device=device).first()

            raw_speed = position.get('speed')
            current_speed = round(float(raw_speed) * 1.852, 1) if raw_speed is not None else 0.0
            current_vibration = attributes.get('vibration', 0) or attributes.get('shock', 0)

            lat = position.get('latitude')
            lon = position.get('longitude')

            is_alarm = False
            save_type = None
            called_number = None

            # قاموس العناوين باللغات الثلاث
            titles = {
                "ar": f"تنبيه أمان من: {device.name}",
                "en": f"Security Alert from: {device.name}",
                "tr": f"Güvenlik Uyarısı: {device.name}"
            }
            bodies = {"ar": "", "en": "", "tr": ""}

            # 🚨 [1] فحص استغاثة الطوارئ SOS (أولوية قصوى)
            if event_type == "sos" or alarm_type == "sos" or raw_evt_code == "1":
                is_alarm = True
                save_type = 'sos'
                sos_index = attributes.get('index') or attributes.get('sos_index')
                if not sos_index:
                    time_threshold = timezone.now() - timedelta(minutes=3)
                    recent_sos_count = GPSAlarmLog.objects.filter(
                        device=device,
                        event_type='sos',
                        created_at__gte=time_threshold
                    ).count()
                    sos_index = (recent_sos_count % 3) + 1

                called_number = f"رقم الطوارئ SOS رقم ({sos_index})"
                bodies = {
                    "ar": f"🚨 نداء استغاثة SOS فوري! جاري الاتصال برقم الطوارئ ({sos_index}).",
                    "en": f"🚨 Immediate SOS Distress Call! Calling emergency number ({sos_index}).",
                    "tr": f"🚨 Acil SOS Çağrısı! ({sos_index}) numaralı acil durum hattı aranıyor."
                }

            # 🔑 [2] فحص تشغيل/إطفاء المحرك (ACC) أو قطع الكهرباء (يسبق السياج لتجنب التداخل)
            elif event_type in ["ignitionOn", "ignitionOff"] or alarm_type in ["ignition", "powercut", "poweroff"] or raw_evt_code in ["26", "27", "5"]:
                if alarm_type in ["powercut", "poweroff"] or raw_evt_code == "5":
                    is_alarm = True
                    save_type = 'ignition'
                    bodies = {
                        "ar": "حالة المركبة: تم فصل بطارية / كهرباء الجهاز ⚠️",
                        "en": "Vehicle Status: Device power / battery disconnected ⚠️",
                        "tr": "Araç Durumu: Cihazın elektriği / aküsü kesildi ⚠️"
                    }
                elif getattr(device, 'acc_alert_active', True):
                    is_alarm = True
                    save_type = 'ignition'
                    is_on = (event_type == "ignitionOn" or alarm_type == "ignition" or raw_evt_code == "26")
                    if is_on:
                        bodies = {
                            "ar": "حالة المركبة: تم تشغيل محرك السيارة 🟢",
                            "en": "Vehicle Status: Vehicle engine turned ON 🟢",
                            "tr": "Araç Durumu: Araç motoru çalıştırıldı 🟢"
                        }
                    else:
                        bodies = {
                            "ar": "حالة المركبة: تم إطفاء محرك السيارة 🔴",
                            "en": "Vehicle Status: Vehicle engine turned OFF 🔴",
                            "tr": "Araç Durumu: Araç motoru durduruldu 🔴"
                        }

            # 🏃‍♂️ [3] فحص تجاوز السرعة
            elif speed_setting and speed_setting.is_active and (
                current_speed > speed_setting.max_speed or
                event_type == "deviceOverspeed" or
                raw_evt_code == "22"
            ):
                is_alarm = True
                save_type = 'speed'
                bodies = {
                    "ar": f"⚠️ تجاوز حد السرعة! السيارة تسير بسرعة ({current_speed} كم/س)، والحد الأقصى هو ({speed_setting.max_speed} كم/س).",
                    "en": f"⚠️ Speed Limit Exceeded! Vehicle speed is ({current_speed} km/h), max allowed is ({speed_setting.max_speed} km/h).",
                    "tr": f"⚠️ Hız Sınırı Aşıldı! Araç hızı ({current_speed} km/s), izin verilen sınır ({speed_setting.max_speed} km/s)."
                }

            # 📳 [4] فحص الاهتزاز
            elif vibration_setting and vibration_setting.is_active and (
                alarm_type in ["vibration", "tampering", "shock", "movement"] or
                raw_evt_code.lstrip('0') in ['1', '35']
            ):
                is_alarm = True
                save_type = 'shock'
                bodies = {
                    "ar": f"📳 تحذير اهتزاز! تم رصد اهتزاز أو محاولة تلاعب بالمركبة ({device.name}).",
                    "en": f"📳 Vibration Warning! Vibration or tampering detected on vehicle ({device.name}).",
                    "tr": f"📳 Titreşim Uyarısı! ({device.name}) aracında titreşim veya müdahale algılandı."
                }

            # 📍 [5] فحص السياج الجغرافي (Geofence)
            elif geofence_setting and geofence_setting.is_active:
                is_exit = event_type == "geofenceExit" or alarm_type == "geofenceexit"
                is_enter = event_type == "geofenceEnter" or alarm_type == "geofenceenter"

                if not (is_exit or is_enter) and lat is not None and lon is not None:
                    if geofence_setting.center_lat and geofence_setting.center_lon and geofence_setting.radius:
                        distance = calculate_distance_meters(
                            float(lat),
                            float(lon),
                            float(geofence_setting.center_lat),
                            float(geofence_setting.center_lon)
                        )
                        if distance > float(geofence_setting.radius):
                            is_exit = True

                if is_exit:
                    is_alarm = True
                    save_type = 'geofence'
                    bodies = {
                        "ar": f"📍 تنبيه السياج الجغرافي! المركبة ({device.name}) خرجت من النطاق المحدد.",
                        "en": f"📍 Geofence Alert! Vehicle ({device.name}) exited the designated zone.",
                        "tr": f"📍 Coğrafi Sınır Uyarısı! ({device.name}) belirlenen bölgeden çıktı."
                    }
                elif is_enter:
                    is_alarm = True
                    save_type = 'geofence'
                    bodies = {
                        "ar": f"📍 تنبيه السياج الجغرافي! المركبة ({device.name}) دخلت إلى النطاق المحدد.",
                        "en": f"📍 Geofence Alert! Vehicle ({device.name}) entered the designated zone.",
                        "tr": f"📍 Coğrafi Sınır Uyarısı! ({device.name}) belirlenen bölgeye girdi."
                    }

            if is_alarm and save_type:
                profile = UserProfile.objects.filter(user=user).first()
                user_lang = profile.language if (profile and profile.language in ['ar', 'en', 'tr']) else 'ar'
                notif_title = titles.get(user_lang, titles['ar'])
                notif_body = bodies.get(user_lang, bodies['ar'])

                alarm_log = GPSAlarmLog.objects.create(
                    device=device,
                    event_type=save_type,
                    message=notif_body,
                    latitude=lat,
                    longitude=lon,
                    speed=current_speed,
                )

                tokens = list(
                    UserDeviceToken.objects.filter(user=user).values_list('fcm_token', flat=True)
                )

                if tokens:
                    multicast_msg = messaging.MulticastMessage(
                        notification=messaging.Notification(
                            title=notif_title,
                            body=notif_body,
                        ),
                        data={
                            "alarm_id": str(alarm_log.id),
                            "device_id": str(device.id),
                            "event_type": str(save_type),
                            "language": str(user_lang),
                            "called_number": str(called_number or ""),
                        },
                        tokens=tokens,
                    )
                    messaging.send_each_for_multicast(multicast_msg)

                return JsonResponse({
                    'status': 'processed',
                    'event_type': save_type,
                    'language': user_lang,
                    'message': notif_body
                }, status=200)

            return JsonResponse({'status': 'ignored', 'message': 'No active alarm condition matched'}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON payload'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

@csrf_exempt
def traccar_webhook_25_11(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'success', 'message': 'Webhook endpoint is active'}, status=200)
        
    if request.method == 'POST':
        try:
            if not request.body:
                return JsonResponse({'status': 'ignored', 'message': 'Empty body received'}, status=400)
                
            data = json.loads(request.body.decode('utf-8'))

            event = data.get('event') or {}
            position = data.get('position') or {}
            traccar_device_info = data.get('device') or {}
            
            event_type = event.get('type', '')
            pos_attrs = position.get('attributes') or {}
            evt_attrs = event.get('attributes') or {}
            attributes = {**pos_attrs, **evt_attrs}
            alarm_type = str(attributes.get('alarm', '')).lower()
            
            traccar_id = event.get('deviceId') or traccar_device_info.get('id')
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)
            
            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)
            
            speed_setting = SpeedSettings.objects.filter(device=device).first()
            vibration_setting = VibrationSettings.objects.filter(device=device).first()
            geofence_setting = GeofenceSettings.objects.filter(device=device).first()

            raw_speed = position.get('speed')
            current_speed = round(float(raw_speed) * 1.852, 1) if raw_speed is not None else 0.0
            current_vibration = attributes.get('vibration', 0) or attributes.get('shock', 0)
            
            is_alarm = False
            save_type = None
            called_number = None

            # قاموس العناوين باللغات الثلاث
            titles = {
                "ar": f"تنبيه أمان من: {device.name}",
                "en": f"Security Alert from: {device.name}",
                "tr": f"Güvenlik Uyarısı: {device.name}"
            }
            bodies = {"ar": "", "en": "", "tr": ""}

                        # 🚨 [1] فحص استغاثة الطوارئ SOS (أولوية قصوى)
            if event_type == "sos" or alarm_type == "sos":
                is_alarm = True
                save_type = 'sos'
                sos_index = attributes.get('index') or attributes.get('sos_index')
                if not sos_index:
                    time_threshold = timezone.now() - timedelta(minutes=3)
                    recent_sos_count = GPSAlarmLog.objects.filter(device=device, event_type='sos', created_at__gte=time_threshold).count()
                    sos_index = (recent_sos_count % 3) + 1 
                
                called_number = f"رقم الطوارئ SOS رقم ({sos_index})"
                bodies = {
                    "ar": f"🚨 نداء استغاثة SOS فوري! جاري الاتصال برقم الطوارئ ({sos_index}).",
                    "en": f"🚨 Immediate SOS Distress Call! Calling emergency number ({sos_index}).",
                    "tr": f"🚨 Acil SOS Çağrısı! ({sos_index}) numaralı acil durum hattı aranıyor."
                }

                        # 🔑 فحص تشغيل/إطفاء المحرك (ACC) أو قطع الكهرباء
            raw_evt_code = str(attributes.get('event', ''))
            if event_type in ["ignitionOn", "ignitionOff"] or alarm_type in ["ignition", "powercut", "poweroff"] or raw_evt_code in ["26", "27"]:
                if alarm_type in ["powercut", "poweroff"]:
                    is_alarm = True
                    save_type = 'ignition'
                    bodies = {
                        "ar": "حالة المركبة: تم فصل بطارية / كهرباء الجهاز ⚠️",
                        "en": "Vehicle Status: Device power / battery disconnected ⚠️",
                        "tr": "Araç Durumu: Cihazın elektriği / aküsü kesildi ⚠️"
                    }
                elif getattr(device, 'acc_alert_active', True):
                    is_alarm = True
                    save_type = 'ignition'
                    is_on = (event_type == "ignitionOn" or alarm_type == "ignition" or raw_evt_code == "26")
                    if is_on:
                        bodies = {
                            "ar": "حالة المركبة: تم تشغيل محرك السيارة 🟢",
                            "en": "Vehicle Status: Vehicle engine turned ON 🟢",
                            "tr": "Araç Durumu: Araç motoru çalıştırıldı 🟢"
                        }
                    else:
                        bodies = {
                            "ar": "حالة المركبة: تم إطفاء محرك السيارة 🔴",
                            "en": "Vehicle Status: Vehicle engine turned OFF 🔴",
                            "tr": "Araç Durumu: Araç motoru durduruldu 🔴"
                        }

            # 🏃‍♂️ [3] فحص تجاوز السرعة
            elif speed_setting and speed_setting.is_active and (current_speed > speed_setting.max_speed or event_type == "deviceOverspeed"):
                is_alarm = True
                save_type = 'speed'
                bodies = {
                    "ar": f"⚠️ تجاوز حد السرعة! السيارة تسير بسرعة ({current_speed} كم/س)، والحد الأقصى هو ({speed_setting.max_speed} كم/س).",
                    "en": f"⚠️ Speed Limit Exceeded! Vehicle speed is ({current_speed} km/h), max allowed is ({speed_setting.max_speed} km/h).",
                    "tr": f"⚠️ Hız Sınırı Aşıldı! Araç hızı ({current_speed} km/s), izin verilen sınır ({speed_setting.max_speed} km/s)."
                }

            # 📳 [4] فحص الاهتزاز
            elif vibration_setting and vibration_setting.is_active and (
                alarm_type in ["vibration", "tampering", "shock", "movement"] or 
                str(attributes.get('event', '')).lstrip('0') == '1'
            ):
                is_alarm = True
                save_type = 'shock'
                bodies = {
                    "ar": f"📳 تحذير اهتزاز! تم رصد اهتزاز أو محاولة تلاعب بالمركبة ({device.name}).",
                    "en": f"📳 Vibration Warning! Vibration or tampering detected on vehicle ({device.name}).",
                    "tr": f"📳 Titreşim Uyarısı! ({device.name}) aracında titreşim veya müdahale algılandı."
                }

            # 🗺️ [5] فحص السياج الجغرافي (دائري ومستطيل)
            elif geofence_setting and geofence_setting.is_active:
                out_of_bounds = (
                    event_type in ["geofenceExit"] or 
                    alarm_type in ["geofenceExit", "geofence"] or 
                    attributes.get('out_geofence', False)
                )
                
                lat, lon = position.get('latitude'), position.get('longitude')
                
                # نفحص الإحداثيات رياضياً فقط إذا لم يكن هناك حدث آخر محدد
                if not out_of_bounds and lat is not None and lon is not None and event_type in ["", "deviceMoving", "deviceUnknown"]:
                    if geofence_setting.geofence_type == "circle":
                        if geofence_setting.center_latitude and geofence_setting.center_longitude and geofence_setting.max_radius:
                            dist_meters = calculate_distance_meters(
                                lat, lon,
                                geofence_setting.center_latitude,
                                geofence_setting.center_longitude
                            )
                            if dist_meters > geofence_setting.max_radius:
                                out_of_bounds = True
                                
                    elif geofence_setting.geofence_type == "rectangle":
                        if all([geofence_setting.ne_latitude, geofence_setting.ne_longitude, 
                                geofence_setting.sw_latitude, geofence_setting.sw_longitude]):
                            is_outside_lat = not (geofence_setting.sw_latitude <= lat <= geofence_setting.ne_latitude)
                            is_outside_lon = not (geofence_setting.sw_longitude <= lon <= geofence_setting.ne_longitude)
                            if is_outside_lat or is_outside_lon:
                                out_of_bounds = True

                if out_of_bounds:
                    is_alarm = True
                    save_type = 'geofence'
                    if geofence_setting.geofence_type == "circle" and geofence_setting.max_radius:
                        bodies = {
                            "ar": f"🗺️ تجاوز النطاق! خرجت السيارة عن محيط السياج الجغرافي المسموح به ({geofence_setting.max_radius} متر).",
                            "en": f"🗺️ Geofence Exit! Vehicle exited the allowed perimeter ({geofence_setting.max_radius} meters).",
                            "tr": f"🗺️ Bölge İhlali! Araç izin verilen güvenli alan sınırının ({geofence_setting.max_radius} metre) dışına çıktı."
                        }
                    else:
                        bodies = {
                            "ar": "🗺️ تجاوز النطاق! خرجت السيارة عن حدود المنطقة الجغرافية المحددة.",
                            "en": "🗺️ Geofence Exit! Vehicle exited the designated geographic zone.",
                            "tr": "🗺️ Bölge İhlali! Araç belirlenen coğrafi bölge sınırlarının dışına çıktı."
                        }

            if not is_alarm:
                return JsonResponse({
                    'status': 'ignored', 
                    'message': f'Within safety range (Speed: {current_speed}, Vib: {current_vibration})'
                }, status=200)

            

            # 💾 حفظ السجل في قاعدة البيانات (يمكنك حفظها كـ JSON أو إضافة حقول للغات)
            GPSAlarmLog.objects.create(
                device=device,
                event_type=save_type,
                title=json.dumps(titles, ensure_ascii=False), # حفظ اللغات الثلاث
                body=json.dumps(bodies, ensure_ascii=False),   # حفظ اللغات الثلاث
                called_sos_number=called_number,
                current_speed=current_speed
            )

              # 🚀 إرسال الإشعار عبر Firebase
            fcm_tokens = list(UserDeviceToken.objects.filter(user=user).values_list('fcm_token', flat=True))
            if fcm_tokens:
                # تجهيز الكائن بالصيغة المطلوبة
                notification_payload = {
                    "title": titles,
                    "body": bodies
                }

                android_settings = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id=str(save_type).upper(),
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        sound="default"
                    )
                )
                
                message = messaging.MulticastMessage(
                    # 1. لإظهار الإشعار تلقائياً في شريط الإشعارات (باللغة العربية كافتراضي مثلاً)
                    notification=messaging.Notification(
                        title=titles["ar"],
                        body=bodies["ar"]
                    ),
                    # 2. إرسال اللغات الثلاث بالهيكلية التي طلبتها تماماً لكي يقرأها تطبيق Flutter
                    data={
                        "device_id": str(device.id),
                        "event_type": str(save_type).upper(),
                        "notification": json.dumps(notification_payload, ensure_ascii=False),
                        "click_action": "FLUTTER_NOTIFICATION_CLICK"
                    },
                    android=android_settings,
                    tokens=fcm_tokens,
                )
                
                response = messaging.send_each_for_multicast(message)

                
              
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success:
                            err_str = str(resp.exception)
                            if getattr(resp.exception, 'code', '') == 'invalid-argument' or 'not-registered' in err_str:
                                UserDeviceToken.objects.filter(fcm_token=fcm_tokens[idx]).delete() # 32 مسافة (داخل if)

            return JsonResponse({'status': 'success', 'message': 'Alarm evaluated, logged, and notification sent'}, status=200) # 12 مسافة (داخل try)

        except Exception as e: # 8 مسافات (موازية لـ try)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500) # 12 مسافة (داخل except)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405) # 4 مسافات (داخل def)


########################
import json
import firebase_admin
from firebase_admin import messaging
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import GPSDevice, GPSAlarmLog, UserDeviceToken # تأكد من استيراد الموديل المخصص للتوكنز

# تأكد من تهيئة Firebase Admin في مكان ما عند إقلاع المشروع (مثال: apps.py أو settings.py)
# if not firebase_admin._apps:
#     firebase_admin.initialize_app()


import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
from gps_api.models import GPSDevice, GPSAlarmLog, UserDeviceToken, SpeedSettings, VibrationSettings, GeofenceSettings
# تأكد من استيراد الجداول الجديدة وجلب مكتبة Firebase الخاصة بك (messaging)

from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
import json
from firebase_admin import messaging

# 🔥 الدالة السحرية للتحقق من حفظ الأرقام داخل سيرفر Traccar
def verify_device_attributes_on_traccar(traccar_id, expected_numbers):
    """
    تقوم بجلب بيانات الجهاز الحالية من سيرفر Traccar وفحص الـ attributes 
    للتأكد من أن الأرقام تم تسجيلها في ذاكرة الجهاز المتصل
    """
    url = f"{settings.TRACCAR_SERVER_URL.rstrip('/')}/api/devices?id={traccar_id}"
    auth_credentials = HTTPBasicAuth(settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
    
    try:
        response = requests.get(url, auth=auth_credentials, timeout=10)
        if response.status_code == 200 and response.json():
            device_data = response.json()[0]
            # جلب السمات المرتدة من نظام قراءة الـ gps103 الخاص بـ Coban
            attributes = device_data.get("attributes", {})
            
            # تحويل كافة قيم الـ attributes إلى نصوص لتسهيل البحث عن الأرقام بداخلها
            attributes_string = str(attributes).replace(" ", "")
            
            verified_numbers = []
            for num in expected_numbers:
                clean_num = ''.join(filter(str.isdigit, str(num)))
                # التحقق مما إذا كان الرقم (سواء بالصيغة المحلية أو الدولية) قد ظهر في باكت البيانات
                if clean_num in attributes_string or clean_num[-9:] in attributes_string:
                    verified_numbers.append(num)
            
            return verified_numbers
    except Exception:
        return []
    return []
#ok
def send_traccar_command(traccar_id, command_text):

    url = f"{settings.TRACCAR_SERVER_URL.rstrip('/')}/api/commands/send"

    auth = HTTPBasicAuth(
        settings.TRACCAR_USER,
        settings.TRACCAR_PASSWORD
    )

    payload = {
        "deviceId": int(traccar_id),
        "type": "custom",
        "textChannel": False,
        "attributes": {
            "data": command_text
        }
    }

    try:
        response = requests.post(
            url,
            json=payload,
            auth=auth,
            timeout=15
        )

        if response.status_code == 200:
            return {
                "success": True,
                "status": "sent",
                "http_status": 200
            }

        elif response.status_code == 202:
            return {
                "success": True,
                "status": "queued",
                "http_status": 202,
                "queue": response.json()
            }

        return {
            "success": False,
            "status": "failed",
            "error": response.text
        }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "status": "error",
            "error": str(e)
        }
class ToggleMicView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):

        try:
            device = GPSDevice.objects.get(
                pk=pk,
                user=request.user
            )

        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"},
                status=status.HTTP_404_NOT_FOUND
            )

        mode = request.data.get("mode")

        commands = {
            "monitor": "monitor123456",
            "tracker": "tracker123456"
        }

        if mode not in commands:
            return Response(
                {"error": "يرجى إرسال monitor أو tracker"},
                status=status.HTTP_400_BAD_REQUEST
            )

        result = send_traccar_command(
            device.traccar_id,
            commands[mode]
        )

        if result["success"]:

            return Response({
                "success": True,
                "status": "sent",
                "message": f"تم قبول إرسال أمر {mode} من Traccar",
                "device_name": device.name,
                "imei": device.imei,
                "traccar_id": device.traccar_id,
              #  "command_id": result["command"].get("id")
            }, status=status.HTTP_200_OK)

        return Response({
            "success": False,
            "status": "failed",
            "error": result.get("error")
        }, status=status.HTTP_502_BAD_GATEWAY)
# كلاس الـ API الخاص بتفعيل وإلغاء وضع المايك (التنصت)


# إعداد كلاس التصفيح (Pagination) لجلب 20 سجل في كل صفحة
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class GPSAlarmLogListView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. جلب السجلات التي تخص أجهزة المستخدم الحالي فقط لحماية الخصوصية
        queryset = GPSAlarmLog.objects.filter(device__user=request.user)
        
        # 2. إمكانية التصفية بحسب جهاز معين إذا أرسل المطور device_id في الرابط
        device_id = request.query_params.get('device_id')
        if device_id:
            try:
                # التأكد أولاً أن الجهاز يخص المستخدم
                device = GPSDevice.objects.get(id=device_id, user=request.user)
                queryset = queryset.filter(device=device)
            except GPSDevice.DoesNotExist:
                return Response({"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه"}, status=status.HTTP_403_FORBIDDEN)
        
        # 3. إمكانية التصفية بحسب نوع الإنذار (sos, overspeed, geofence, vibration)
        event_type = request.query_params.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        # 4. تطبيق التصفيح (Pagination) على البيانات
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        
        # 5. صياغة البيانات المرجعة يدويًا بشكل مخصص ومناسب للتطبيق
        logs_data = []
        for log in page:
            logs_data.append({
                "id": log.id,
                "device_id": log.device.id,
                "device_name": log.device.name,
                "event_type": log.event_type,
                "event_type_display": log.get_event_type_display(),
                "title": log.title,
                "body": log.body,
                "called_sos_number": log.called_sos_number, # الرقم التتابعي المتصل به في حال الـ SOS
                "current_speed": log.current_speed,
                "created_at": log.created_at.strftime('%Y-%m-%d %H:%M:%S') # تنسيق الوقت بشكل واضح للتطبيق
            })

        return paginator.get_paginated_response(logs_data)



from django.shortcuts import get_object_or_404


class ClearDeviceAlarmLogsView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, device_id):
        # 1. التحقق من وجود الجهاز
        device = get_object_or_404(GPSDevice, id=device_id)

        # 2. جلب السجلات الأساسية للجهاز
        logs_queryset = GPSAlarmLog.objects.filter(device=device)

        # 3. استخراج أنواع التنبيهات المسموحة من الموديل مباشرة
        valid_alarm_types = dict(GPSAlarmLog.ALARM_TYPES)

        # التحقق مما إذا تم تمرير event_type في الرابط (مثل: ?event_type=sos أو ?event_type=sos,overspeed)
        event_type_param = request.query_params.get('event_type')

        if event_type_param:
            # دعم تمرير أكثر من نوع مفصولين بفاصلة
            requested_types = [
                t.strip() for t in event_type_param.split(',') if t.strip()
            ]

            # التحقق من أن جميع الأنواع المرسلة صحيحة ومطابقة لـ ALARM_TYPES
            invalid_types = [
                t for t in requested_types if t not in valid_alarm_types
            ]
            if invalid_types:
                return Response(
                    {
                        'status': 'error',
                        'message': f"نوع التنبيه غير صالح: {', '.join(invalid_types)}",
                        'allowed_types': list(valid_alarm_types.keys()),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # فلترة السجلات حسب الأنواع المطلوبة
            logs_queryset = logs_queryset.filter(
                event_type__in=requested_types
            )

        # 4. تنفيذ الحذف
        deleted_count, _ = logs_queryset.delete()

        return Response(
            {
                'status': 'success',
                'message': f'تم حذف {deleted_count} سجل بنجاح.',
                'deleted_count': deleted_count,
                'filtered_by': event_type_param if event_type_param else 'all',
            },
            status=status.HTTP_200_OK,
        )

# 3. الـ View المسؤولة عن حذف السجل (سجل سجل)
class DeleteGPSAlarmLogAPIView(DestroyAPIView):
    """
    API لحذف سجل تنبيه واحد محدد عن طريق معرفه (pk) في الرابط
    """
    queryset = GPSAlarmLog.objects.all()
    serializer_class = GPSAlarmLogSerializer
    
    # استخدام الـ Token للتحقق من الهوية وفرض الصلاحية المخصصة
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAlarmLogOwner]

    def destroy(self, request, *args, **kwargs):
        # جلب سجل التنبيه بعد التحقق تلقائياً من الصلاحيات (IsAlarmLogOwner)
        instance = self.get_object()
        log_id = instance.id
        
        # تنفيذ عملية الحذف من قاعدة البيانات المحلية
        self.perform_destroy(instance)
        
        # إرجاع رد نجاح مخصص ومفهوم للمستخدم
        return Response({
            "message": f"تم حذف سجل التنبيه ذو المعرف {log_id} بنجاح."
        }, status=status.HTTP_200_OK)


class GPSAlarmLogDeviceListView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    # استقبال device_id كمعامل أساسي في الدالة
    def get(self, request, device_id):
        
        # 1. التأكد أن الجهاز موجود ويخص المستخدم الحالي (حماية الخصوصية)
        try:
            device = GPSDevice.objects.get(id=device_id, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه"}, 
                status=status.HTTP_403_FORBIDDEN
            )

        # 2. جلب السجلات الخاصة بهذا الجهاز المحدد
        queryset = GPSAlarmLog.objects.filter(device=device)

        # 3. إمكانية التصفية الاختيارية بحسب نوع الإنذار (sos, overspeed, geofence, vibration)
        event_type = request.query_params.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        # 4. تطبيق التصفيح (Pagination) على البيانات
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)

        # 5. صياغة البيانات المرجعة يدويًا بشكل مخصص ومناسب للتطبيق
        logs_data = []
        for log in page:
            logs_data.append({
                "id": log.id,
                "device_id": log.device.id,
                "device_name": log.device.name,
                "event_type": log.event_type,
                "event_type_display": log.get_event_type_display(),
                "title": log.title,
                "body": log.body,
                "called_sos_number": log.called_sos_number,
                "current_speed": log.current_speed,
                "created_at": log.created_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        return paginator.get_paginated_response(logs_data)



from django.contrib.auth.hashers import make_password, check_password
from .models import GPSDevice

# 1. API إنشاء أو تعديل الـ PIN Code
class CreateOrUpdatePinCodeView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو لا تملك الصلاحية"}, status=status.HTTP_404_NOT_FOUND)
        
        pin = request.data.get("pin")
        
        # التحقق من شروط الـ PIN (أن يكون أرقام فقط وبطول مناسب)
        if not pin or not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
            return Response({"error": "يجب أن يتكون رمز الـ PIN من 4 إلى 6 أرقام فقط"}, status=status.HTTP_400_BAD_REQUEST)
        
        # تشفير الـ PIN وحفظه بشكل آمن
        device.pin_code = make_password(pin)
        device.save()
        
        return Response({"message": "تم تعيين رمز الـ PIN الخاص بالجهاز بنجاح"}, status=status.HTTP_200_OK)


# 2. API التحقق من الـ PIN Code (قبل تنفيذ الأوامر الحساسة)
class VerifyPinCodeView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو لا تملك الصلاحية"}, status=status.HTTP_404_NOT_FOUND)
        
        pin = request.data.get("pin")
        
        if not device.pin_code:
            return Response({"error": "لم يتم تعيين رمز PIN لهذا الجهاز مسبقاً، يرجى تعيينه أولاً"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not pin:
            return Response({"error": "حقل الـ pin مطلوب"}, status=status.HTTP_400_BAD_REQUEST)
        
        # فحص مطابقة الـ PIN المرسل مع الهاش المشفر بقاعدة البيانات
        if check_password(pin, device.pin_code):
            return Response({
                "status": "success",
                "message": "تم التحقق من الرمز بنجاح، مصرح لك بتنفيذ الأمر الحساس"
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "failed",
                "error": "رمز الـ PIN غير صحيح، تم رفض العملية"
            }, status=status.HTTP_404_NOT_FOUND)

import requests
import hashlib
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from .models import GPSDevice, GPSTrip  # تأكد من مطابقة مسار الاستيراد لمجلد تطبيقك

# -------------------------------------------------------------
# 1. API المزامنة والسحب التلقائي من Traccar (POST)
# -------------------------------------------------------------
class SyncTraccarTripsView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, device_id):
        # 1. التحقق من وجود الجهاز وصلاحية المستخدم الحالي لحماية الخصوصية
        try:
            device = GPSDevice.objects.get(id=device_id, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        if not device.traccar_id:
            return Response(
                {"error": "هذا الجهاز غير مربوط بنظام Traccar (حقل traccar_id مفقود)."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. تحديد النطاق الزمني لجلب الرحلات (مثال: آخر 7 أيام)
        end_period = timezone.now()
        start_period = end_period - timezone.timedelta(days=7)
        
        # صياغة التوقيت بالشكل القياسي النظيف الذي يفهمه سيرفر Traccar (ISO 8601 UTC)
        from_time = start_period.strftime('%Y-%m-%dT%H:%M:%SZ')
        to_time = end_period.strftime('%Y-%m-%dT%H:%M:%SZ')

        # 3. بناء الرابط الأساسي وإعداد المعاملات بشكل مستقل (Query Parameters)
        base_url = settings.TRACCAR_SERVER_URL.rstrip('/')
        endpoint = f"{base_url}/api/reports/trips"
        
        # تمرير المعاملات كمفاتيح داخل قاموس يمنع تماماً مشاكل الترميز ورموز HTML الزائدة مثل &amp;
        query_params = {
            'deviceId': int(device.traccar_id),
            'from': from_time,
            'to': to_time
        }
        
        auth = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        try:
            # إرسال الطلب مع تمرير المعاملات عبر الدالة params لتركيب الرابط برمز & الأصلي
            response = requests.get(endpoint, params=query_params, auth=auth, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return Response(
                    {"error": f"فشل الاتصال بسيرفر Traccar: {response.text}"}, 
                    status=status.HTTP_502_BAD_GATEWAY
                )
            
            traccar_trips = response.json()
            
        except requests.exceptions.RequestException as e:
            return Response(
                {"error": f"خطأ أثناء الاتصال بالشبكة مع سيرفر Traccar: {str(e)}"}, 
                status=status.HTTP_502_BAD_GATEWAY
            )

        # 4. معالجة الرحلات القادمة وحفظها تلقائياً في قاعدة البيانات مع منع التكرار
        saved_count = 0
        for trip_data in traccar_trips:
            s_time = parse_datetime(trip_data.get('startTime'))
            e_time = parse_datetime(trip_data.get('endTime'))
            
            # توليد بصمة فريدة (Unique MD5 Hash) لكل رحلة لمنع تكرار السجلات عند إعادة المزامنة
            raw_sig = f"{device.traccar_id}_{trip_data.get('startTime')}_{trip_data.get('endTime')}"
            signature = hashlib.md5(raw_sig.encode('utf-8')).hexdigest()

            # التحقق إذا كانت الرحلة مسجلة مسبقاً يتم تخطيها فوراً
            if GPSTrip.objects.filter(trip_signature=signature).exists():
                continue

            # تحويل المسافة من متر (وحدة Traccar الافتراضية) إلى كيلومتر
            distance_km = round(trip_data.get('distance', 0) / 1000.0, 2)

            # تسجيل الرحلة تلقائياً في قاعدة البيانات
            GPSTrip.objects.create(
                device=device,
                start_lat=trip_data.get('startLat'),
                start_lon=trip_data.get('startLon'),
                end_lat=trip_data.get('endLat'),
                end_lon=trip_data.get('endLon'),
                start_time=s_time,
                end_time=e_time,
                distance=distance_km,
                duration=trip_data.get('duration', 0),
                trip_signature=signature
            )
            saved_count += 1

        return Response({
            "status": "success",
            "message": "تمت مزامنة وسحب الرحلات من Traccar بنجاح.",
            "total_fetched_from_traccar": len(traccar_trips),
            "new_saved_trips": saved_count
        }, status=status.HTTP_200_OK)


from rest_framework.views import APIView
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import GPSDevice, GPSTrip


class GPSTripListView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_user_device(self, user, device_id):
        """دالة مساعدة للتحقق من وجود الجهاز وملكيته للمستخدم"""
        try:
            return GPSDevice.objects.get(id=device_id, user=user)
        except GPSDevice.DoesNotExist:
            return None

    # 1. جلب جميع الرحلات (GET)
    def get(self, request, device_id, trip_id=None):
        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response(
                {"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه."},
                status=status.HTTP_403_FORBIDDEN
            )

        trips = GPSTrip.objects.filter(device=device).order_by('-start_time')

        trips_data = []
        for trip in trips:
            trips_data.append({
                "id": trip.id,
                "start_location": {"lat": trip.start_lat, "lon": trip.start_lon},
                "end_location": {"lat": trip.end_lat, "lon": trip.end_lon},
                "distance_km": trip.distance,
                "duration_seconds": trip.duration,
                "duration_minutes": round(trip.duration / 60, 2),
                "start_time": trip.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                "end_time": trip.end_time.strftime('%Y-%m-%d %H:%M:%S')
            })

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "total_trips_in_db": len(trips_data),
            "trips": trips_data
        }, status=status.HTTP_200_OK)

    # 2. حذف رحلة واحدة أو مجموعة رحلات أو الكل (DELETE)
    def delete(self, request, device_id, trip_id=None):
        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response(
                {"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه."},
                status=status.HTTP_403_FORBIDDEN
            )

        # الحالة أ: حذف رحلة واحدة محددة عبر الرابط (URL)
        if trip_id is not None:
            deleted_count, _ = GPSTrip.objects.filter(id=trip_id, device=device).delete()
            if deleted_count == 0:
                return Response(
                    {"error": "الرحلة غير موجودة أو تم حذفها مسبقاً."},
                    status=status.HTTP_404_NOT_FOUND
                )
            return Response({
                "status": "success",
                "message": "تم حذف الرحلة بنجاح.",
                "deleted_count": deleted_count
            }, status=status.HTTP_200_OK)

        # الحالة ب: حذف مجموعة رحلات محددة عبر قائمة IDs في الجسم (Body)
        trip_ids = request.data.get("trip_ids")
        if isinstance(trip_ids, list) and trip_ids:
            deleted_count, _ = GPSTrip.objects.filter(id__in=trip_ids, device=device).delete()
            return Response({
                "status": "success",
                "message": f"تم حذف ({deleted_count}) رحلة بنجاح.",
                "deleted_count": deleted_count
            }, status=status.HTTP_200_OK)

        # الحالة ج: حذف جميع رحلات الجهاز دفعة واحدة (مع شرط تأكيد صريح للحماية)
        delete_all = str(request.data.get("delete_all", "")).lower() in ["true", "1"]
        if delete_all:
            deleted_count, _ = GPSTrip.objects.filter(device=device).delete()
            return Response({
                "status": "success",
                "message": "تم حذف جميع رحلات الجهاز بنجاح.",
                "deleted_count": deleted_count
            }, status=status.HTTP_200_OK)

        # في حال إرسال طلب DELETE بدون تحديد رحلة أو تأكيد الحذف الشامل
        return Response(
            {"error": "يرجى تمرير رقم الرحلة في الرابط، أو إرسال قائمة 'trip_ids'، أو 'delete_all': true."},
            status=status.HTTP_400_BAD_REQUEST
        )

# -------------------------------------------------------------
# 2. API عرض سجل الحركة / الرحلات المخزنة لجهاز محدد (GET)
# -------------------------------------------------------------
class GPSTripListView_1(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, device_id):
        # 1. التأكد أولاً من ملكية الجهاز لحماية خصوصية بيانات المستخدمين
        try:
            device = GPSDevice.objects.get(id=device_id, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز المحدد غير موجود أو غير مصرح لك بالوصول إليه."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        # 2. جلب جميع الرحلات الخاصة بهذا الجهاز وترتيبها من الأحدث للأقدم
        trips = GPSTrip.objects.filter(device=device).order_by('-start_time')
        
        # 3. صياغة المخرجات بشكل مخصص ومنظم ومناسب لواجهات التطبيق
        trips_data = []
        for trip in trips:
            trips_data.append({
                "id": trip.id,
                "start_location": {"lat": trip.start_lat, "lon": trip.start_lon},
                "end_location": {"lat": trip.end_lat, "lon": trip.end_lon},
                "distance_km": trip.distance,
                "duration_seconds": trip.duration,
                "duration_minutes": round(trip.duration / 60, 2), # تحويل المدة لدقائق لسهولة القراءة
                "start_time": trip.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                "end_time": trip.end_time.strftime('%Y-%m-%d %H:%M:%S')
            })

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "total_trips_in_db": len(trips_data),
            "trips": trips_data
        }, status=status.HTTP_200_OK)

from rest_framework.decorators import api_view, authentication_classes, permission_classes
@api_view(['GET']) 
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_full_battery_status(request, device_id):
    """
    جلب حالة بطارية السيارة وبطارية الجهاز الداخلية معاً
    """
    ##endpoint = f"{TRACCAR_URL}/api/positions"
    ####
    
    
    endpoint = f"{settings.TRACCAR_SERVER_URL}api/positions"
    

    params = {
                    'deviceId': device_id,
                }

    try:
        response = requests.get(
            endpoint, 
            params=params, 
            auth = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=5
        )
        
        if response.status_code == 200:
            positions = response.json()
            if not positions:
                return JsonResponse({"error": "No data found"}, status=404)
            
            latest_position = positions[-1]
            attributes = latest_position.get('attributes', {})
            
            # 1. بطارية السيارة الخارجية (قيمة الجهد بالفولت مثل 12.6V أو 13.8V أثناء الشحن)
            car_battery_voltage = attributes.get('power') 
            
            # 2. بطارية الجهاز الداخلية (نسبة مئوية)
            device_battery_level = attributes.get('batteryLevel') or attributes.get('battery')
            
            # تحليل حالة بطارية السيارة بناءً على الفولت (للسيارات 12 فولت)
            car_battery_status = "Unknown"
            if car_battery_voltage:
                if car_battery_voltage >= 13.2:
                    car_battery_status = "Running / Charging" # السيارة تعمل والدينامو يشحن
                elif 12.2 <= car_battery_voltage < 13.2:
                    car_battery_status = "Good / Engine Off"   # السيارة مطفأة والبطارية ممتازة
                elif 11.5 <= car_battery_voltage < 12.2:
                    car_battery_status = "Low Battery"         # البطارية ضعيفة
                else:
                    car_battery_status = "Very Low / Dead"     # البطارية شبه ميتة أو الجهاز مفصول عن السيارة
            
            return JsonResponse({
                "device_id": device_id,
                "car_battery": {
                    "voltage": car_battery_voltage,      # مثال: 12.6
                    "status": car_battery_status         # حالة البطارية تحليلياً
                },
                "device_battery": {
                    "level": device_battery_level        # مثال: 90 (%)
                },
                "ignition": attributes.get('ignition'),   # حالة المفتاح/المحرك (True/False)
                "device_time": latest_position.get('deviceTime')
            })
            
        else:
            return JsonResponse({"error": f"Traccar error: {response.status_code}"}, status=response.status_code)
            
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Connection failed: {str(e)}"}, status=500)
    
import requests
from django.http import JsonResponse
from requests.auth import HTTPBasicAuth

# إعدادات الاتصال بسيرفر Traccar
@api_view(['GET']) 
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_device_battery_status(request, device_id):
    """
    جلب حالة البطارية لجهاز معين باستخدام الـ ID الخاص به في Traccar
    """
    endpoint = f"{settings.TRACCAR_SERVER_URL}api/positions"
           
    #endpoint = f"{TRACCAR_URL}/api/positions"
    
    # نطلب من التراكير جلب آخر موقع مسجل لهذا الجهاز بالتحديد
    params = {
        'deviceId': device_id,
    }
    print ("end po",endpoint ,"dev",device_id)
    try:
        response = requests.get(
            endpoint, 
            params=params, 
            auth = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=5
        )
        
        if response.status_code == 200:
            positions = response.json()
            
            if not positions:
                return JsonResponse({"error": "No position data found for this device"}, status=404)
            
            # جلب آخر موقع مسجل (يكون عادة آخر عنصر في القائمة أو العنصر الوحيد حسب الطلب)
            latest_position = positions[-1]
            attributes = latest_position.get('attributes', {})
            
            # جهاز PGC 303 غالباً يرسل البطارية كنسبة مئوية (batteryLevel) أو كجهد كهربائي (battery)
            battery_level = attributes.get('batteryLevel') or attributes.get('battery')
            
            return JsonResponse({
                "device_id": device_id,
                "battery": battery_level, # القيمة المرتجعة (مثال: 85 تعني 85%)
                "device_time": latest_position.get('deviceTime'),
                "valid": latest_position.get('valid')
            })
            
        else:
            return JsonResponse({"error": f"Traccar error: {response.status_code}"}, status=response.status_code)
            
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Connection failed: {str(e)}"}, status=500)

import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from requests.auth import HTTPBasicAuth
import json




@csrf_exempt
def safe_engine_stop(request, device_id):
    """
    API لإيقاف السيارة بأمان (فقط إذا كانت السرعة أقل من 20 كم/ساعة)
    """
    if request.method != 'POST':
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)
        
    auth = HTTPBasicAuth(settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
    
    
    try:
        # الخطوة 1: فحص السرعة الحالية للجهاز من خلال آخر موقع (Positions)
        pos_endpoint = f"{settings.TRACCAR_SERVER_URL}/api/positions"
      
       
        pos_response = requests.get(pos_endpoint, params={'deviceId': device_id}, auth=auth, timeout=5)
        print(pos_response)
        if pos_response.status_code != 200 or not pos_response.json():
            return JsonResponse({"error": "Could not fetch current device position/speed"}, status=400)
            
        latest_position = pos_response.json()[-1]
        
        # تراكير يحفظ السرعة بالعقدة البحرية (Knots)، نحولها إلى كم/ساعة عن طريق ضربها في 1.852
        speed_knots = latest_position.get('speed', 0)
        speed_kmh = float(speed_knots) * 1.852
        
        # الخطوة 2: التحقق من شرط الأمان (السرعة أقل من 20 كم/ساعة)
        if speed_kmh >= 20:
            return JsonResponse({
                "status": "rejected",
                "message": f"❌ لا يمكن إيقاف السيارة لأسباب أمنية. السرعة الحالية مرتفعة جداً: {speed_kmh:.1f} كم/ساعة.",
                "current_speed": round(speed_kmh, 1)
            }, status=400)
            
        # الخطوة 3: إرسال أمر الإيقاف لـ Traccar (إذا تحقق الشرط)
        cmd_endpoint = f"{settings.TRACCAR_SERVER_URL}/api/commands/send"
        
        # بناء جسم الأمر الموجه لجهاز PGC 303 (بروتوكول تراكير يستخدم الـ Relay)
        command_payload = {
            "deviceId": device_id,
            "type": "engineStop", # أو "relay" حسب إعدادات الـ القالب في تراكير
            "attributes": {}
        }
        
        cmd_response = requests.post(
            cmd_endpoint, 
            json=command_payload, 
            auth=auth, 
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        if cmd_response.status_code ==200 :
            return JsonResponse({
                "status": "success",
                "message": f" Done! تم إرسال أمر إيقاف المحرك بنجاح. السرعة عند التنفيذ: {speed_kmh:.1f} كم/ساعة.",
                "current_speed": round(speed_kmh, 1)
            }, status=200)
        else:
            return JsonResponse({
                "error": f"Traccar failed to send command. Status: {cmd_response.status_code}",
                "traccar_response": cmd_response.text
            }, status=500)
            
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Connection to Traccar failed: {str(e)}"}, status=500)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_device_odometer(request, device_id):
    """جلب الإحصائيات اللحظية (الحالية + الكلية الحية بدون ازدواجية)"""
    device = get_object_or_404(GPSDevice, id=device_id, user=request.user)
    odometer, _ = DeviceOdometer.objects.get_or_create(device=device)
    
    # تحديث الرقم القياسي للسرعة إذا أرسل التطبيق سرعة حالية أعلى
    live_speed = float(request.query_params.get('current_speed', 0.0))
    if live_speed > odometer.max_speed_record:
        odometer.max_speed_record = live_speed
        odometer.save(update_fields=['max_speed_record'])

    return Response({
        "device_id": device.id,
        "last_reset_at": odometer.last_reset_at,
        "current_trip": odometer.get_current_trip_stats(),
        "total_stats": odometer.get_live_total_stats(),
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reset_device_odometer(request, device_id):
    """تصفير عداد الرحلة الحالية ونقل قيمتها إلى الأرشيف التراكمي"""
    device = get_object_or_404(GPSDevice, id=device_id, user=request.user)
    
    with transaction.atomic():
        odometer, _ = DeviceOdometer.objects.select_for_update().get_or_create(device=device)
        
        # 1. حساب المسافة والمدة المتراكمة منذ آخر تصفير
        current = odometer.get_current_trip_stats()
        
        # دعم وضع الاختبار الوهمي (إذا أرسل التطبيق مسافة وهمية في الـ Request)
        simulated_dist = float(request.data.get('simulated_distance', 0.0))
        simulated_max_speed = float(request.data.get('current_max_speed', 0.0))
        
        distance_to_archive = current["distance"] if current["distance"] > 0 else simulated_dist
        
        # 2. ترحيل القيم إلى الأرشيف التراكمي
        odometer.archived_distance = round(odometer.archived_distance + distance_to_archive, 2)
        odometer.archived_duration += current["duration"]
        
        if simulated_max_speed > odometer.max_speed_record:
            odometer.max_speed_record = simulated_max_speed
            
        # 3. تحديث نقطة التصفير الزمنية (تصفر الرحلة الحالية فوراً)
        odometer.last_reset_at = timezone.now()
        odometer.save()

    return Response({
        "status": "success",
        "message": "تم تصفير عداد الرحلة الحالية وترحيل المسافة إلى الإجمالي بنجاح",
        "current_trip": odometer.get_current_trip_stats(),   # ستعود 0.0 تلقائياً
        "total_stats": odometer.get_live_total_stats(),      # ستبقى محتفظة بالرقم الكلي بدون نقصان أو ازدواجية
    }, status=status.HTTP_200_OK)

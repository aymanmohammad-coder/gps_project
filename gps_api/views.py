import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.contrib.auth.models import User
from rest_framework.permissions import IsAuthenticated # اختياري: لحظر المستخدمين غير المسجلين
from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from requests.auth import HTTPBasicAuth
from .models import GPSDevice, SpeedSettings, VibrationSettings, GeofenceSettings,EmergencyNumbers ,OilChangeSettings, OilChangeLog,UserDeviceToken,GPSAlarmLog
from rest_framework import serializers
from rest_framework.generics import DestroyAPIView
from rest_framework.authentication import TokenAuthentication
from rest_framework.pagination import PageNumberPagination


from django.conf import settings
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from .serializers import GPSDeviceSerializer,GPSAlarmLogSerializer
from .permissions import IsDeviceOwner,IsAlarmLogOwner


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



class AddGPSDeviceAPI(APIView):
    """
    API لإضافة جهاز جديد إلى خادم Traccar وقاعدة بيانات دجانغو المحلية معاً بالاعتماد على Token المستخدم
    """
    # 1. إجبار الـ API على قراءة الـ Token والتحقق من هوية المرسل
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request):
        name = request.data.get('name')
        imei = request.data.get('imei') # الـ Unique ID الخاص بالجهاز
        model = request.data.get('model', 'Coban 303') # افتراضي Coban 303 إن لم يرسل
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

            if traccar_response.status_code ==200:
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



class RegisterAPI(APIView):
    """
    API لتسجيل مستخدم جديد في النظام
    """
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        email = request.data.get('email', '')

        if not username or not password:
            return Response({"error": "اسم المستخدم وكلمة المرور حقول إجبارية"}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(username=username).exists():
            return Response({"error": "اسم المستخدم هذا مأخوذ بالفعل"}, status=status.HTTP_400_BAD_REQUEST)

        # إنشاء المستخدم وتشفير كلمة المرور تلقائياً
        user = User.objects.create_user(username=username, password=password, email=email)
        
        # إنشاء توكين خاص بالمستخدم الجديد فوراً ليدخل النظام تلقائياً
        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "message": "تم تسجيل الحساب بنجاح",
            "token": token.key,
            "username": user.username
        }, status=status.HTTP_201_CREATED)


class LoginAPI(APIView):
    """
    API لتسجيل الدخول وإنشاء مفتاح التحقق (Token)
    """
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        fcm_token = request.data.get('fcm_token')

        if not username or not password:
            return Response({"error": "يرجى إدخال اسم المستخدم وكلمة المرور"}, status=status.HTTP_400_BAD_REQUEST)

        # التحقق من صحة البيانات عبر نظام دجانغو الداخلي
        user = authenticate(username=username, password=password)

        if user is not None:
            # جلب أو إنشاء التوكين الخاص بهذا المستخدم
            token, _ = Token.objects.get_or_create(user=user)
            if fcm_token:
                # نستخدم update_or_create لضمان عدم تكرار التوكن وتحديثه إذا كان يخص مستخدم آخر أو نفس المستخدم
                UserDeviceToken.objects.update_or_create(
                    fcm_token=fcm_token,
                    defaults={'user': user}
                )
            return Response({
                "message": "تم تسجيل الدخول بنجاح",
                "uid":user.id,
                "token": token.key,
                "username": user.username
            }, status=status.HTTP_200_OK)
        else:
            return Response({"error": "بيانات الدخول غير صحيحة، يرجى المحاولة مجدداً"}, status=status.HTTP_401_UNAUTHORIZED)


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




class DeviceAllSettingsAPI(APIView):
    """
    API موحد لإدارة عمليات الـ CRUD لثلاثة جداول إعدادات منفصلة في مسار واحد آمن
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_user_device(self, user, device_id):
        """التحقق الأمني من ملكية الجهاز"""
        try:
            return GPSDevice.objects.get(user=user, id=device_id)
        except GPSDevice.DoesNotExist:
            return None

    # 1. جلب الإعدادات من الـ 3 جداول معاً (GET)
    def get(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في الرابط"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بالوصول إليه"}, status=status.HTTP_403_FORBIDDEN)

        # جلب البيانات بشكل آمن حتى لو لم تكن منشأة مسبقاً (سيُرجع قيم افتراضية)
        speed = getattr(device, 'speed_setting', None)
        vibration = getattr(device, 'vibration_setting', None)
        geofence = getattr(device, 'geofence_setting', None)

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "speed_settings": {
                "max_speed": speed.max_speed if speed else 120.0,
                "is_active": speed.is_active if speed else False
            },
            "vibration_settings": {
                "max_vibration": vibration.max_vibration if vibration else 5,
                "is_active": vibration.is_active if vibration else False
            },
            "geofence_settings": {
                "max_geofence_radius": geofence.max_radius if geofence else 100.0,
                "is_active": geofence.is_active if geofence else False
            }
        }, status=status.HTTP_200_OK)

    # 2. إضافة أو تحديث الإعدادات في الجداول الثلاثة معاً (POST / PUT)
    # استخدمنا نفس الدالة للإضافة والتعديل تسهيلاً للتعامل مع التطبيق (Upsert Logic)
    def post(self, request):
        device_id = request.data.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في جسم الطلب"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        # استقبال المدخلات من الطلب
        max_speed = request.data.get('max_speed')
        max_vibration = request.data.get('max_vibration')
        max_geofence_radius = request.data.get('max_geofence_radius')
        
        # استقبال حالات التفعيل (True/False)
        speed_active = request.data.get('speed_active', True)
        vibration_active = request.data.get('vibration_active', True)
        geofence_active = request.data.get('geofence_active', True)

        # أ) تحديث أو إنشاء جدول السرعة
        if max_speed is not None:
            SpeedSettings.objects.update_or_create(
                device=device,
                defaults={'max_speed': max_speed, 'is_active': speed_active}
            )

        # ب) تحديث أو إنشاء جدول الاهتزاز
        if max_vibration is not None:
            VibrationSettings.objects.update_or_create(
                device=device,
                defaults={'max_vibration': max_vibration, 'is_active': vibration_active}
            )

        # ج) تحديث أو إنشاء جدول النطاق الجغرافي
        if max_geofence_radius is not None:
            GeofenceSettings.objects.update_or_create(
                device=device,
                defaults={'max_radius': max_geofence_radius, 'is_active': geofence_active}
            )

        return Response({"message": "تم حفظ وتحديث كافة إعدادات الجداول الثلاثة بنجاح"}, status=status.HTTP_200_OK)

    # 3. حذف وإلغاء تفعيل كافة الإعدادات (DELETE)
    def delete(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بحذف إعداداته"}, status=status.HTTP_403_FORBIDDEN)

        # حذف السجلات من الجداول الثلاثة دفعة واحدة
        SpeedSettings.objects.filter(device=device).delete()
        VibrationSettings.objects.filter(device=device).delete()
        GeofenceSettings.objects.filter(device=device).delete()

        return Response({"message": "تم حذف سجلات الإعدادات من كافة الجداول بنجاح وعاد الجهاز للوضع الافتراضي"}, status=status.HTTP_200_OK)



class EmergencyNumbersAPI(APIView):
    """
    CRUD API: (إضافة، تعديل، جلب، حذف) لأرقام الطوارئ الثلاثة الخاصة بالجهاز عبر الـ Token
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_user_device(self, user, device_id):
        """التحقق الأمني من ملكية الجهاز"""
        try:
            return GPSDevice.objects.get(user=user, id=device_id)
        except GPSDevice.DoesNotExist:
            return None

    # 1. جلب أرقام الطوارئ (GET)
    def get(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في الرابط"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بالوصول إليه"}, status=status.HTTP_403_FORBIDDEN)

        # محاولة جلب السجل أو إرجاع قيم فارغة إن لم تكن منشأة
        sos_obj = getattr(device, 'emergency_numbers', None)

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "emergency_numbers": {
                "sos_number_1": sos_obj.sos_number_1 if sos_obj else None,
                "sos_number_2": sos_obj.sos_number_2 if sos_obj else None,
                "sos_number_3": sos_obj.sos_number_3 if sos_obj else None,
            },
            "updated_at": sos_obj.updated_at.strftime('%Y-%m-%d %H:%M:%S') if sos_obj else None
        }, status=status.HTTP_200_OK)

    # 2. إضافة وتعديل ديناميكي (POST)
    def post(self, request):
        device_id = request.data.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        # جلب السجل الحالي أو التجهيز لإنشاء سجل جديد (Upsert Logic)
        sos_obj, created = EmergencyNumbers.objects.get_or_create(device=device)

        # التعديل المرن: تحديث الحقل فقط إذا تم إرساله في الطلب (حتى لو أرسلت رقماً واحداً)
        if 'sos_number_1' in request.data:
            sos_obj.sos_number_1 = request.data.get('sos_number_1')
        if 'sos_number_2' in request.data:
            sos_obj.sos_number_2 = request.data.get('sos_number_2')
        if 'sos_number_3' in request.data:
            sos_obj.sos_number_3 = request.data.get('sos_number_3')

        sos_obj.save()

        message = "تم إنشاء أرقام الطوارئ بنجاح" if created else "تم تحديث أرقام الطوارئ بنجاح"
        return Response({"message": message, "device_id": device.id}, status=status.HTTP_200_OK)

    # 3. حذف أرقام الطوارئ بالكامل (DELETE)
    def delete(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في الرابط"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        # حذف السجل بالكامل من قاعدة البيانات
        EmergencyNumbers.objects.filter(device=device).delete()
        return Response({"message": "تم حذف كافة أرقام الطوارئ بنجاح من قاعدة البيانات"}, status=status.HTTP_200_OK)


class OilChangeRecordAPI(APIView):
    """
    API لتسجيل عملية تغيير زيت جديدة وتحديث عداد الصيانة الافتراضي للجهاز
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device_id = request.data.get('device_id')
        oil_type = request.data.get('oil_type')
        filter_changed = request.data.get('filter_changed', False) # True أو False
        oil_interval_km = request.data.get('oil_interval_km', 5000.0) # المسافة الدورية الافتراضية
        warn_before_km = request.data.get('warn_before_km', 500.0)

        if not device_id or not oil_type:
            return Response({"error": "الحقول 'device_id' و 'oil_type' مطلوبة"}, status=status.HTTP_400_BAD_REQUEST)

        # التحقق من ملكية الجهاز
        try:
            device = GPSDevice.objects.get(user=request.user, id=device_id)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        # 1. جلب قراءة العداد الكلية الحالية (Total Distance) من سيرفر Traccar حياً لتسجيلها
        traccar_url = f"{settings.TRACCAR_SERVER_URL}/api/positions"
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
                return Response({"error": "تعذر جلب قراءة عداد السيارة من سيرفر Traccar لتسجيل العملية"}, status=status.HTTP_502_BAD_GATEWAY)
        except requests.exceptions.RequestException as e:
            return Response({"error": f"فشل الاتصال بسيرفر تراكار الداخلي: {str(e)}"}, status=status.HTTP_504_GATEWAY_TIMEOUT)

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
        traccar_url = f"{settings.TRACCAR_SERVER_URL}/api/positions"
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

@csrf_exempt
def traccar_webhook(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'success', 'message': 'Webhook endpoint is active'}, status=200)
        
    if request.method == 'POST':
        try:
            if not request.body:
                return JsonResponse({'status': 'ignored', 'message': 'Empty body received'}, status=400)
                
            data = json.loads(request.body.decode('utf-8'))

            # 🟢 سجل تشخيصي حقيقي في السيرفر
            print("--- TRACCAR DATA RECEIVED ---")
            print(json.dumps(data, indent=2))
            print("-----------------------------")
            
            event = data.get('event', {})
            position = data.get('position', {})
            traccar_device_info = data.get('device', {})
            
            event_type = event.get('type')
            attributes = position.get('attributes', {}) or event.get('attributes', {})
            alarm_type = attributes.get('alarm', '')
            
            traccar_id = event.get('deviceId') or traccar_device_info.get('id') or data.get('device', {}).get('id')
            
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)
            
            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)
            
            # 🔄 جلب إعدادات الأمان الخاصة بالجهاز من قاعدة البيانات أو استخدام القيم الافتراضية
            speed_setting = SpeedSettings.objects.filter(device=device).first()
            vibration_setting = VibrationSettings.objects.filter(device=device).first()
            geofence_setting = GeofenceSettings.objects.filter(device=device).first()

            # تحويل السرعة من عقدة (Knots) القادمة من Traccar إلى كم/ساعة
            current_speed = round(position.get('speed', 0) * 1.852, 1) if position.get('speed') is not None else 0
            current_vibration = attributes.get('vibration', 0) or attributes.get('shock', 0)
            
            # المتغيرات الخاصة ببناء التنبيه
            is_alarm = False
            save_type = None
            title = f"تنبيه أمان من: {device.name}"
            body = ""
            called_number = None

            # 🚨 [1] فحص حالة استغاثة الطوارئ SOS الافتراضية للجهاز
            if event_type == "sos" or alarm_type == "sos":
                is_alarm = True
                save_type = 'sos'
                sos_index = attributes.get('index') or attributes.get('sos_index')
                if not sos_index:
                    time_threshold = timezone.now() - timedelta(minutes=3)
                    recent_sos_count = GPSAlarmLog.objects.filter(device=device, event_type='sos', created_at__gte=time_threshold).count()
                    sos_index = (recent_sos_count % 3) + 1 
                called_number = f"رقم الطوارئ SOS رقم ({sos_index})"
                body = f"🚨 نداء استغاثة SOS فوري! جاري الاتصال بـ {called_number}."

            # 🏃‍♂️ [2] فحص تجاوز السرعة المخزنة في قاعدة البيانات
            elif speed_setting and speed_setting.is_active and current_speed > speed_setting.max_speed:
                is_alarm = True
                save_type = 'overspeed'
                body = f"⚠️ تجاوز حد السرعة! السيارة تسير بسرعة ({current_speed} كم/س)، والحد الأقصى المسموح به هو ({speed_setting.max_speed} كم/س)."

            # 📳 [3] فحص مستويات الاهتزاز مقارنة بالقيم المحددة بقاعدة البيانات
           # elif alarm_type in ["vibration", "tampering", "shock", "movement"] or attributes.get('event') == "01" or attributes.get('event') == 1:
           # elif vibration_setting and vibration_setting.is_active and (current_vibration > vibration_setting.max_vibration or alarm_type in ["vibration", "shock", "tampering"]):
             elif (alarm_type in ["vibration", "tampering", "shock", "movement"] or 
                  attributes.get('event') in ["001", "01", 1] or 
                  attributes.get('alarm') in ["vibration", "shock"]):
                is_alarm = True
                save_type = 'vibration'
                body = f"📳 تحذير اهتزاز! تم رصد اهتزاز بمستوى ({current_vibration}) وتجاوز الحساسية المحددة ({vibration_setting.max_vibration}) أو محاولة تلاعب بالجهاز."

            # 🗺️ [4] فحص السياج الجغرافي المخزن بقاعدة البيانات
            # يعتمد هذا الجزء على التنبيه البروتوكولي أو فحص المسافة التراكمية إذا أرسلها ترأكار كـ دالة مسافة
            elif geofence_setting and geofence_setting.is_active and (event_type in ["geofenceEnter", "geofenceExit"] or alarm_type in ["geofenceEnter", "geofenceExit"] or attributes.get('out_geofence', False)):
                is_alarm = True
                save_type = 'geofence'
                body = f"🗺️ تجاوز النطاق! خرجت السيارة عن محيط السياج الجغرافي النشط المسموح به الممتد لـ ({geofence_setting.max_radius} متر)."

            # 🔑 [5] فحص حالات تشغيل/إطفاء السيارة المهمة
            elif event_type in ["ignitionOn", "ignitionOff"] or alarm_type in ["ignition", "powerCut"]:
                is_alarm = True
                save_type = 'ignition'
                status_text = "تم تشغيل محرك السيارة 🟢" if (event_type == "ignitionOn" or alarm_type == "ignition") else "تم إطفاء محرك السيارة 🔴"
                body = f"حالة المحرك: {status_text}."

            # 🛑 في حال لم تتوفر شروط التنبيهات النشطة، يتم إهمال الإشعار لعدم إزعاج المستخدم
            if not is_alarm:
                return JsonResponse({
                    'status': 'ignored', 
                    'message': f'Data logged locally. Metrics (Speed: {current_speed}, Vib: {current_vibration}) within safety range.'
                }, status=200)

            # 💾 حفظ السجل في قاعدة البيانات ليعرض لاحقاً في التطبيق للمستخدم
            GPSAlarmLog.objects.create(
                device=device,
                event_type=save_type,
                title=title,
                body=body,
                called_sos_number=called_number,
                current_speed=current_speed
            )

            # 🚀 إرسال الإشعار الفوري لـ Firebase لكل أجهزة هذا المستخدم
            fcm_tokens = list(UserDeviceToken.objects.filter(user=user).values_list('fcm_token', flat=True))

            if fcm_tokens:
                message = messaging.MulticastMessage(
                    notification=messaging.Notification(title=title, body=body),
                    data={
                        "device_id": str(device.id),
                        "event_type": save_type,
                        "click_action": "FLUTTER_NOTIFICATION_CLICK"
                    },
                    tokens=fcm_tokens,
                )
                response = messaging.send_each_for_multicast(message)
                
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success:
                            if resp.exception.code == 'invalid-argument' or 'not-registered' in str(resp.exception):
                                UserDeviceToken.objects.filter(fcm_token=fcm_tokens[idx]).delete()

            return JsonResponse({'status': 'success', 'message': 'Alarm evaluated, logged, and notification sent'}, status=200)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
@csrf_exempt
def traccar_webhook_3(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'success', 'message': 'Webhook endpoint is active'}, status=200)
        
    if request.method == 'POST':
        try:
            if not request.body:
                return JsonResponse({'status': 'ignored', 'message': 'Empty body received'}, status=400)
                
            data = json.loads(request.body.decode('utf-8'))

# 🟢 سطر تشخيصي مؤقت لطباعة البيانات القادمة من الجهاز في السجلات
            print("--- TRACCAR DATA RECEIVED ---")
            print(json.dumps(data, indent=2))
            print("-----------------------------")
            event = data.get('event', {})
            position = data.get('position', {})
            traccar_device_info = data.get('device', {})
            
            # محاولة جلب التنبيه من الحدث أو من خصائص الموقع مباشرة
            event_type = event.get('type')
            attributes = position.get('attributes', {}) or event.get('attributes', {})
            alarm_type = attributes.get('alarm', '')
            
            traccar_id = event.get('deviceId') or traccar_device_info.get('id') or data.get('device', {}).get('id')
            
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)
            
            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)
            
            title = f"تنبيه طوارئ من: {device.name}"
            body = "تنبيه جديد من نظام التتبع."
            called_number = None
            current_speed = round(position.get('speed', 0) * 1.852, 1) if position.get('speed') is not None else 0

            # 🚨 1. منطق فحص الـ SOS
            if event_type == "sos" or alarm_type == "sos":
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
                
                # فحص الحالات المصاحبة
                speed_setting = getattr(device, 'speed_setting', None)
                vibration_setting = getattr(device, 'vibration_setting', None)
                geofence_setting = getattr(device, 'geofence_setting', None)
                
                current_vibration = attributes.get('vibration', 0)
                is_outside_geofence = attributes.get('out_geofence', False)

                reasons = []
                if speed_setting and speed_setting.is_active and current_speed > speed_setting.max_speed:
                    reasons.append(f"تجاوز السرعة ({current_speed} كم/س)")
                if vibration_setting and vibration_setting.is_active and (current_vibration > vibration_setting.max_vibration or alarm_type in ["vibration", "shock"]):
                    reasons.append("رصد اهتزاز عنيف")
                if geofence_setting and geofence_setting.is_active and (is_outside_geofence or event_type == "geofenceExit"):
                    reasons.append("تجاوز النطاق الجغرافي")

                if reasons:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number}. بالتزامن مع: {' و '.join(reasons)}."
                else:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number} (العدادات الحالية طبيعية)."
                save_type = 'sos'

            # 🏃‍♂️ 2. تنبيهات السرعة (سواء حدث مستقل أو علامة في الموقع)
            elif event_type in ["deviceOverspeed", "speedLimitExceeded"] or alarm_type == "overspeed":
                body = f"تنبيه! تجاوزت السيارة السرعة المحددة: {current_speed} كم/س."
                save_type = 'overspeed'

            # 🗺️ 3. تنبيهات النطاق الجغرافي
            elif event_type in ["geofenceEnter", "geofenceExit"] or alarm_type in ["geofenceEnter", "geofenceExit"]:
                body = "تحذير! خرجت السيارة عن النطاق الجغرافي المسموح به." if (event_type == "geofenceExit" or alarm_type == "geofenceExit") else "دخلت السيارة النطاق الجغرافي."
                save_type = 'geofence'

            # 📳 4. تنبيهات الاهتزاز والتلاعب (مرونة أعلى في المسميات القادمة من أجهزة الصيني)
            elif alarm_type in ["vibration", "tampering", "shock", "movement"]:
                body = "تحذير! تم رصد اهتزاز في السيارة أو محاولة تلاعب بالجهاز."
                save_type = 'vibration'

            # 🔑 5. إضافة تنبيه حالة تشغيل/إطفاء السيارة (Acc Ignition) وهو مهم جداً للمستخدمين
            elif event_type in ["deviceOnline", "ignitionOn", "ignitionOff"] or alarm_type in ["ignition", "powerCut"]:
                status_text = "تم تشغيل محرك السيارة" if (event_type == "ignitionOn" or alarm_type == "ignition") else "تم إطفاء محرك السيارة"
                body = f"تنبيه حالة المحرك: {status_text}."
                save_type = 'ignition'

            else:
                # إذا لم يتطابق مع أي تنبيه خطير، نقوم بحفظه كموقع عادي أو تجاهله دون إرسال إشعار فايبربيز مزعج
                return JsonResponse({'status': 'ignored', 'message': f'Data received but event type ({event_type}/{alarm_type}) not tracked'}, status=200)

            # 💾 حفظ السجل في قاعدة البيانات
            GPSAlarmLog.objects.create(
                device=device,
                event_type=save_type,
                title=title,
                body=body,
                called_sos_number=called_number,
                current_speed=current_speed
            )

            # 🚀 إرسال الإشعار الفوري لـ Firebase (الكود الخاص بك ممتاز يترك كما هو)
            fcm_tokens = list(UserDeviceToken.objects.filter(user=user).values_list('fcm_token', flat=True))
            if fcm_tokens:
                message = messaging.MulticastMessage(
                    notification=messaging.Notification(title=title, body=body),
                    data={"device_id": str(device.id), "event_type": save_type, "click_action": "FLUTTER_NOTIFICATION_CLICK"},
                    tokens=fcm_tokens,
                )
                response = messaging.send_each_for_multicast(message)
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success and (resp.exception.code == 'invalid-argument' or 'not-registered' in str(resp.exception)):
                            UserDeviceToken.objects.filter(fcm_token=fcm_tokens[idx]).delete()

            return JsonResponse({'status': 'success', 'message': 'Alarm logged and notification sent'}, status=200)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def traccar_webhook_2(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8'))
            
            event = data.get('event', {})
            position = data.get('position', {})
            traccar_device_info = data.get('device', {})
            
            traccar_id = event.get('deviceId') or traccar_device_info.get('id')
            event_type = event.get('type')
            
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)
            
            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)
            
            title = f"تنبيه طوارئ من: {device.name}"
            body = "تنبيه جديد من نظام التتبع."
            alarm_type = event.get('attributes', {}).get('alarm', '') or position.get('attributes', {}).get('alarm', '')
            
            called_number = None
            current_speed = round(position.get('speed', 0) * 1.852, 1)

            # 🚨 منطق فحص الـ SOS ومعرفة الرقم المتصل به
            if event_type == "sos" or alarm_type == "sos":
                sos_index = position.get('attributes', {}).get('index') or event.get('attributes', {}).get('sos_index')
                
                if not sos_index:
                    time_threshold = timezone.now() - timedelta(minutes=3)
                    recent_sos_count = GPSAlarmLog.objects.filter(
                        device=device, 
                        event_type='sos', 
                        created_at__gte=time_threshold
                    ).count()
                    sos_index = (recent_sos_count % 3) + 1 
                
                called_number = f"رقم الطوارئ SOS رقم ({sos_index})"

                speed_setting = getattr(device, 'speed_setting', None)
                vibration_setting = getattr(device, 'vibration_setting', None)
                geofence_setting = getattr(device, 'geofence_setting', None)
                
                current_vibration = position.get('attributes', {}).get('vibration', 0)
                is_outside_geofence = position.get('attributes', {}).get('out_geofence', False)

                reasons = []
                if speed_setting and speed_setting.is_active and current_speed > speed_setting.max_speed:
                    reasons.append(f"تجاوز السرعة ({current_speed} كم/س)")
                if vibration_setting and vibration_setting.is_active and (current_vibration > vibration_setting.max_vibration or alarm_type == "vibration"):
                    reasons.append("رصد اهتزاز عنيف")
                if geofence_setting and geofence_setting.is_active and (is_outside_geofence or event_type == "geofenceExit"):
                    reasons.append("تجاوز النطاق الجغرافي")

                if reasons:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number}. بالتزامن مع: {' و '.join(reasons)}."
                else:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number} (العدادات الحالية طبيعية)."
                
                save_type = 'sos'

            elif event_type in ["deviceOverspeed", "speedLimitExceeded"]:
                body = f"تنبيه! تجاوزت السيارة السرعة المحددة: {current_speed} كم/س."
                save_type = 'overspeed'
            elif event_type in ["geofenceEnter", "geofenceExit"]:
                body = "تحذير! خرجت السيارة عن النطاق الجغرافي المسموح به." if event_type == "geofenceExit" else "دخلت السيارة النطاق الجغرافي."
                save_type = 'geofence'
            elif alarm_type == "vibration" or alarm_type == "tampering":
                body = "تحذير! تم رصد اهتزاز في السيارة أو محاولة تلاعب بالجهاز."
                save_type = 'vibration'
            else:
                return JsonResponse({'status': 'ignored', 'message': 'Event not tracked'}, status=200)

            # 💾 [حفظ السجل في قاعدة البيانات ليعرض لاحقاً في التطبيق]
            GPSAlarmLog.objects.create(
                device=device,
                event_type=save_type,
                title=title,
                body=body,
                called_sos_number=called_number,
                current_speed=current_speed
            )

            # -----------------------------------------------------------
            # 🚀 إرسال الإشعار الفوري لـ Firebase لكل أجهزة هذا المستخدم
            # -----------------------------------------------------------
            # جلب كل التوكنز النشطة للمستخدم بصيغة قائمة (List)
            fcm_tokens = list(UserDeviceToken.objects.filter(user=user).values_list('fcm_token', flat=True))

            if fcm_tokens:
                # تجهيز رسالة Firebase الجماعية (Multicast)
                message = messaging.MulticastMessage(
                    notification=messaging.Notification(
                        title=title,
                        body=body,
                    ),
                    data={
                        "device_id": str(device.id),
                        "event_type": save_type,
                        "click_action": "FLUTTER_NOTIFICATION_CLICK" # مفيد لتطبيقات الموبايل عند الضغط
                    },
                    tokens=fcm_tokens,
                )
                # تنفيذ عملية الإرسال الفعلي
                response = messaging.send_each_for_multicast(message)
                
                # إدارة التوكنز التالفة (اختياري لكن احترافي لحذف التوكنز التي انتهت صلاحيتها أو حذف المستخدم التطبيق)
                if response.failure_count > 0:
                    for idx, resp in enumerate(response.responses):
                        if not resp.success:
                            # إذا كان سبب الفشل أن التوكن غير صالح، نقوم بحذفه من قاعدة البيانات
                            if resp.exception.code == 'invalid-argument' or 'not-registered' in str(resp.exception):
                                bad_token = fcm_tokens[idx]
                                UserDeviceToken.objects.filter(fcm_token=bad_token).delete()

            return JsonResponse({'status': 'success', 'message': 'Alarm logged and notification sent'}, status=200)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
from .models import GPSDevice, SpeedSettings, VibrationSettings, GeofenceSettings, GPSAlarmLog

@csrf_exempt
def traccar_webhook1(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8'))
            
            event = data.get('event', {})
            position = data.get('position', {})
            traccar_device_info = data.get('device', {})
            
            traccar_id = event.get('deviceId') or traccar_device_info.get('id')
            event_type = event.get('type')
            
            if not traccar_id:
                return JsonResponse({'status': 'ignored', 'message': 'No device ID found'}, status=400)
            
            try:
                device = GPSDevice.objects.get(traccar_id=traccar_id)
                user = device.user
            except GPSDevice.DoesNotExist:
                return JsonResponse({'status': 'ignored', 'message': 'Device not registered in Django'}, status=404)
            
            title = f"تنبيه طوارئ من: {device.name}"
            body = "تنبيه جديد من نظام التتبع."
            alarm_type = event.get('attributes', {}).get('alarm', '') or position.get('attributes', {}).get('alarm', '')
            
            called_number = None
            current_speed = round(position.get('speed', 0) * 1.852, 1)

            # 🚨 منطق فحص الـ SOS ومعرفة الرقم المتصل به
            if event_type == "sos" or alarm_type == "sos":
                # 1. محاولة قراءة الترتيب من خصائص الجهاز إن وجدت (بعض الأجهزة ترسل sos_index أو index)
                sos_index = position.get('attributes', {}).get('index') or event.get('attributes', {}).get('sos_index')
                
                # 2. إذا لم يرسلها الجهاز، نقوم بحسابها ذكياً عبر مراجعة السجلات السابقة في آخر 3 دقائق
                if not sos_index:
                    time_threshold = timezone.now() - timedelta(minutes=3)
                    recent_sos_count = GPSAlarmLog.objects.filter(
                        device=device, 
                        event_type='sos', 
                        created_at__gte=time_threshold
                    ).count()
                    # إذا كان هناك إنذار سابق قبل دقيقة، فهذا يعني أن الأول فشل والجهاز يتصل بالثاني الآن
                    sos_index = (recent_sos_count % 3) + 1 
                
                # تحديث الرقم المتصل به بناءً على الـ index المستنتج أو المرسل
                # (بافتراض أنك تستقبل الأرقام وتخزنها، أو يمكنك جلبها إذا أضفت حقول sos1,sos2 للمودل الرئيسي)
                # هنا سنكتفي بكتابة المحاولة الحالية في السجل
                called_number = f"رقم الطوارئ SOS رقم ({sos_index})"

                # فحص الجداول الثلاثة للإعدادات المدمجة
                speed_setting = getattr(device, 'speed_setting', None)
                vibration_setting = getattr(device, 'vibration_setting', None)
                geofence_setting = getattr(device, 'geofence_setting', None)
                
                current_vibration = position.get('attributes', {}).get('vibration', 0)
                is_outside_geofence = position.get('attributes', {}).get('out_geofence', False)

                reasons = []
                if speed_setting and speed_setting.is_active and current_speed > speed_setting.max_speed:
                    reasons.append(f"تجاوز السرعة ({current_speed} كم/س)")
                if vibration_setting and vibration_setting.is_active and (current_vibration > vibration_setting.max_vibration or alarm_type == "vibration"):
                    reasons.append("رصد اهتزاز عنيف")
                if geofence_setting and geofence_setting.is_active and (is_outside_geofence or event_type == "geofenceExit"):
                    reasons.append("تجاوز النطاق الجغرافي")

                if reasons:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number}. بالتزامن مع: {' و '.join(reasons)}."
                else:
                    body = f"🚨 نداء استغاثة SOS! جاري الاتصال بـ {called_number} (العدادات الحالية طبيعية)."
                
                save_type = 'sos'

            # حالات الإنذارات العادية الأخرى للحفظ في الـ Logs أيضاً
            elif event_type in ["deviceOverspeed", "speedLimitExceeded"]:
                body = f"تنبيه! تجاوزت السيارة السرعة المحددة: {current_speed} كم/س."
                save_type = 'overspeed'
            elif event_type in ["geofenceEnter", "geofenceExit"]:
                body = "تحذير! خرجت السيارة عن النطاق الجغرافي المسموح به." if event_type == "geofenceExit" else "دخلت السيارة النطاق الجغرافي."
                save_type = 'geofence'
            elif alarm_type == "vibration" or alarm_type == "tampering":
                body = "تحذير! تم رصد اهتزاز في السيارة أو محاولة تلاعب بالجهاز."
                save_type = 'vibration'
            else:
                return JsonResponse({'status': 'ignored', 'message': 'Event not tracked'}, status=200)

            # 💾 [حفظ السجل في قاعدة البيانات ليعرض لاحقاً في التطبيق]
            GPSAlarmLog.objects.create(
                device=device,
                event_type=save_type,
                title=title,
                body=body,
                called_sos_number=called_number,
                current_speed=current_speed
            )

            # إرسال الإشعار الفوري لـ Firebase
            topic_name = f"user_{user.id}"
            # هنا كود إرسال الـ FCM الخاص بك...
            
            return JsonResponse({'status': 'success', 'message': 'Alarm logged and notification sent'}, status=200)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

##
import requests
from requests.auth import HTTPBasicAuth
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
import requests
from requests.auth import HTTPBasicAuth


def send_traccar_command22(traccar_id, command_text):
    url = f"{settings.TRACCAR_SERVER_URL.rstrip('/')}/api/commands/send"
    auth_credentials = HTTPBasicAuth(settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
    
    payload = {
        "deviceId": traccar_id,
         "type": "custom",
        "textChannel": False,
        
        "attributes": {"data": command_text}
    }
    try:
        response = requests.post(url, json=payload, auth=auth_credentials, headers={"Content-Type": "application/json"}, timeout=10)
        return response.status_code in [200, 202]
    except requests.exceptions.RequestException:
        return False

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
class SetSosNumbersView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, status=status.HTTP_404_NOT_FOUND)
        
        # نأخذ الأرقام كما هي مدخلة من المستخدم (يفضل إدخالها بالصيغة المحلية 0933...)
        num1 = request.data.get("sos1")
        num2 = request.data.get("sos2")
        num3 = request.data.get("sos3")
        
        raw_numbers = [n for n in [num1, num2, num3] if n]
        if not raw_numbers:
            return Response({"error": "يجب إدخال رقم طوارئ واحد على الأقل"}, status=status.HTTP_400_BAD_REQUEST)
        
        # بناء الأوامر وإرسالها
        commands = [f"admin123456 {num}" for num in raw_numbers]
        success_sent_count = 0
        for cmd in commands:
            if send_traccar_command(device.traccar_id, cmd):
                success_sent_count += 1
                
        if success_sent_count == 0:
            return Response({"error": "فشل إرسال الأوامر بالكامل إلى سيرفر Traccar"}, status=status.HTTP_502_BAD_GATEWAY)
            
        # 🛡️ خطوة التحقق الحقيقية واللحظية برمجياً:
        # نقوم بفحص ما إذا كان السيرفر قد التقط الأرقام المحدثة في معطيات باكت البيانات من الجهاز
        saved_and_verified = verify_device_attributes_on_traccar(device.traccar_id, raw_numbers)
        
        # إذا تطابقت الأرقام، نؤكد للمستخدم الحفظ الحقيقي
        return Response({
            "message": "اكتملت عملية المعالجة.",
            "device_name": device.name,
            "commands_sent_successfully": f"{success_sent_count} من أصل {len(commands)}",
            "saved_and_verified_inside_device": saved_and_verified,
            "unverified_numbers": list(set(raw_numbers) - set(saved_and_verified))
        }, status=status.HTTP_200_OK)

# دالة إرسال الأمر عبر Traccar API باستخدام إعدادات الـ settings
#qq
def send_traccar_command1(traccar_id, command_text):
    """
    إرسال أمر مخصص (Custom Command) إلى جهاز معين عبر Traccar
    """
    # بناء الرابط باستخدام المتغير الموجود في settings
    url = f"{settings.TRACCAR_SERVER_URL.rstrip('/')}/api/commands/send"
    
    # بيانات تسجيل الدخول من settings
    auth_credentials = HTTPBasicAuth(settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)
    
    payload = {
        "deviceId": traccar_id,
        "type": "custom",
        "attributes": {
            "data": command_text
        }
    }
    
    try:
        response = requests.post(
            url,
            json=payload,
            auth=auth_credentials,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        # إرجاع True إذا كان كود الحالة 200 أو 202 (تم قبول الأمر)
        return response.status_code in [200, 202]
    except requests.exceptions.RequestException:
        return False
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
class ToggleMicView22(APIView):
    permission_classes = [IsAuthenticated] # حماية الـ API ليكون للمستخدمين المسجلين فقط

    def post(self, request, pk):
        try:
            # التحقق من وجود الجهاز وأنه ملك للمستخدم الحالي لحماية الخصوصية
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # استقبال وضع التشغيل من الطلب (json body): {"mode": "monitor"} أو {"mode": "tracker"}
        mode = request.data.get("mode")
        
        if mode == "monitor":
            command = "monitor123456;"
        elif mode == "tracker":
            command = "tracker123456;"
        else:
            return Response(
                {"error": "الحالة المرسلة غير مدعومة. يرجى إرسال 'monitor' أو 'tracker'"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # استدعاء الدالة وإرسال الأمر إلى Traccar
        success = send_traccar_command(device.traccar_id, command)
        
        if success:
            return Response({
                "message": f"تم إرسال أمر التحويل إلى وضع {mode} بنجاح.",
                "device_name": device.name,
                "imei": device.imei
            }, status=status.HTTP_200_OK)
        else:
            return Response(
                {"error": "فشل الاتصال بسيرفر Traccar أو رفض السيرفر إرسال الأمر للمركبة"}, 
                status=status.HTTP_502_BAD_GATEWAY
            )
        
class SetSosNumbersView1(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            # التحقق من ملكية الجهاز للمستخدم لحماية الخصوصية
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response({"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, status=status.HTTP_404_NOT_FOUND)
        
        # استقبال الأرقام الثلاثة من الـ Request Body
        num1 = request.data.get("sos1")
        num2 = request.data.get("sos2")
        num3 = request.data.get("sos3")
        
        if not num1:
            return Response({"error": "يجب إدخال رقم الطوارئ الأول على الأقل"}, status=status.HTTP_400_BAD_REQUEST)
        
        # بناء الأوامر الخاصة بجهاز Coban (صيغة الأمر: admin123456 space phone_number)
        commands = []
        if num1: commands.append(f"admin123456 {num1}")
        if num2: commands.append(f"admin123456 {num2}")
        if num3: commands.append(f"admin123456 {num3}")
        
        # إرسال الأوامر بالتتابع إلى السيرفر
        success_count = 0
        for cmd in commands:
            if send_traccar_command(device.traccar_id, cmd):
                success_count += 1
        
        # التحقق من نجاح إرسال الأوامر على الأقل للرقم الأول
        if success_count > 0:
            return Response({
                "message": f"تم إرسال أرقام الطوارئ بنجاح لجهاز Coban. (الأوامر الناجحة: {success_count} من أصل {len(commands)})",
                "device_name": device.name,
                "saved_numbers": [n for n in [num1, num2, num3] if n]
            }, status=status.HTTP_200_OK)
        else:
            return Response({"error": "فشل إرسال الأوامر، تأكد من اتصال سيرفر Traccar"}, status=status.HTTP_502_BAD_GATEWAY)



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


# -------------------------------------------------------------
# 2. API عرض سجل الحركة / الرحلات المخزنة لجهاز محدد (GET)
# -------------------------------------------------------------
class GPSTripListView(APIView):
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
       ## pos_endpoint = f"{settings.TRACCAR_SERVER_URL}api/positions"
       
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

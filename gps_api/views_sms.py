# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import TokenAuthentication
from django.shortcuts import get_object_or_404
from gps_api.models import GPSDevice
from django.http import JsonResponse
import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from django.conf import settings
# استيراد الموديل والدالة العامة
from gps_api.models import GPSDevice ,EmergencyNumbers # تأكد من مسار الموديل الصحيح لديك
from gps_api.utils import send_textbee_sms

# Serializer للتأكد من صحة المدخلات في الـ JSON Body
class MicModeSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=['monitor', 'tracker'], 
        error_messages={'invalid_choice': "الحالة المرسلة غير مدعومة. يرجى إرسال 'monitor' أو 'tracker'"}
    )

from .utils import dispatch_device_command
class ToggleMicView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        device = get_object_or_404(GPSDevice, pk=pk, user=request.user)
        mode = str(request.data.get("mode", "")).strip().lower()

        if mode not in ["monitor", "tracker"]:
            return Response({"error": "يرجى إرسال monitor أو tracker"}, status=status.HTTP_400_BAD_REQUEST)

        success, data, code = dispatch_device_command(device, mode)
        if success:
            return Response({
                "success": True,
                "message": f"تم إرسال أمر {mode} بنجاح إلى ({device.name})",
                **data
            }, status=code)
        return Response({"success": False, "error": data}, status=code)



import time
from .utils import dispatch_device_command, format_syrian_phone
from .models import GPSDevice, SpeedSettings, VibrationSettings, GeofenceSettings


class DeviceAllSettingsAPI(APIView):
    """
    API موحد لإدارة الجداول الثلاثة + إرسال أوامر SMS الفعلية للجهازين (Coban / iStartek)
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_user_device(self, user, device_id):
        try:
            return GPSDevice.objects.get(user=user, id=device_id)
        except GPSDevice.DoesNotExist:
            return None

    # 1. جلب الإعدادات (GET) - كما هو تماماً
    def get(self, request):
        device_id = request.query_params.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في الرابط"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بالوصول إليه"}, status=status.HTTP_403_FORBIDDEN)

        speed = SpeedSettings.objects.filter(device=device).first()
        vibration = VibrationSettings.objects.filter(device=device).first()
        geofence_setting = GeofenceSettings.objects.filter(device=device).first()

        return Response({
            "device_id": device.id,
            "device_name": device.name,
            "device_model": device.model,
            "speed_settings": {
                "max_speed": speed.max_speed if speed else 120.0,
                "is_active": speed.is_active if speed else False
            },
            "vibration_settings": {
                "max_vibration": vibration.max_vibration if vibration else 5,
                "is_active": vibration.is_active if vibration else False
            },
            "geofence_settings": { 
                "geofence_type": geofence_setting.geofence_type if geofence_setting else "circle",
                "geofence_active": geofence_setting.is_active if geofence_setting else False,
                "center_latitude": geofence_setting.center_latitude if geofence_setting else None,
                "center_longitude": geofence_setting.center_longitude if geofence_setting else None,
                "max_geofence_radius": geofence_setting.max_radius if geofence_setting else None,
                "ne_latitude": geofence_setting.ne_latitude if geofence_setting else None,
                "ne_longitude": geofence_setting.ne_longitude if geofence_setting else None,
                "sw_latitude": geofence_setting.sw_latitude if geofence_setting else None,
                "sw_longitude": geofence_setting.sw_longitude if geofence_setting else None
            }
        }, status=status.HTTP_200_OK)

    # 2. حفظ الإعدادات محلياً + إرسال أوامر التفعيل/الإلغاء للجهاز عبر SMS (POST)
    def post(self, request):
        device_id = request.data.get('device_id')
        if not device_id:
            return Response({"error": "حقل 'device_id' مطلوب في جسم الطلب"}, status=status.HTTP_400_BAD_REQUEST)

        device = self._get_user_device(request.user, device_id)
        if not device:
            return Response({"error": "الجهاز غير موجود أو غير مصرح لك بإدارته"}, status=status.HTTP_403_FORBIDDEN)

        max_speed = request.data.get('max_speed')
        max_vibration = request.data.get('max_vibration')
        speed_active = str(request.data.get('speed_active', True)).lower() in ['true', '1']
        vibration_active = str(request.data.get('vibration_active', True)).lower() in ['true', '1']
        
        geofence_type = request.data.get('geofence_type')
        max_geofence_radius = request.data.get('max_geofence_radius')
        center_lat = request.data.get('center_latitude')
        center_lng = request.data.get('center_longitude')
        ne_lat = request.data.get('ne_latitude')
        ne_lng = request.data.get('ne_longitude')
        sw_lat = request.data.get('sw_latitude')
        sw_lng = request.data.get('sw_longitude')
        geofence_active = str(request.data.get('geofence_active', True)).lower() in ['true', '1']

        sms_logs = []

        # أ) تحديث السرعة وإرسال أمر SMS للجهاز
        if max_speed is not None:
            speed_val = int(float(max_speed))
            SpeedSettings.objects.update_or_create(
                device=device,
                defaults={'max_speed': speed_val, 'is_active': speed_active}
            )
            if speed_active:
                ok, res, _ = dispatch_device_command(device, "speed_on", speed=speed_val)
            else:
                ok, res, _ = dispatch_device_command(device, "speed_off")
            sms_logs.append({"setting": "speed", "active": speed_active, "success": ok, "detail": res})
            time.sleep(1.2)

        # ب) تحديث الاهتزاز وإرسال أمر SMS للجهاز
        if max_vibration is not None:
            vib_val = int(float(max_vibration))
            VibrationSettings.objects.update_or_create(
                device=device,
                defaults={'max_vibration': vib_val, 'is_active': vibration_active}
            )
            if vibration_active:
                # ملاحظة: في iStartek نمرر مستوى الحساسية أو نستخدم الافتراضي في القاموس
                ok, res, _ = dispatch_device_command(device, "shock_on")
            else:
                ok, res, _ = dispatch_device_command(device, "shock_off")
            sms_logs.append({"setting": "vibration", "active": vibration_active, "success": ok, "detail": res})
            time.sleep(1.2)

        # ج) تحديث النطاق الجغرافي وإرسال أمر SMS للجهاز
        if any([geofence_type, max_geofence_radius, center_lat, center_lng, ne_lat, sw_lat]) or 'geofence_active' in request.data:
            geofence_defaults = {'is_active': geofence_active}
            if geofence_type is not None:        geofence_defaults['geofence_type'] = geofence_type
            if max_geofence_radius is not None:  geofence_defaults['max_radius'] = max_geofence_radius
            if center_lat is not None:           geofence_defaults['center_latitude'] = center_lat
            if center_lng is not None:           geofence_defaults['center_longitude'] = center_lng
            if ne_lat is not None:               geofence_defaults['ne_latitude'] = ne_lat
            if ne_lng is not None:               geofence_defaults['ne_longitude'] = ne_lng
            if sw_lat is not None:               geofence_defaults['sw_latitude'] = sw_lat
            if sw_lng is not None:               geofence_defaults['sw_longitude'] = sw_lng

            geo_obj, _ = GeofenceSettings.objects.update_or_create(
                device=device,
                defaults=geofence_defaults
            )

            if geofence_active:
                radius_val = int(float(geo_obj.max_radius or 200))
                if device.model == "Istartek_VT200_L" and geo_obj.center_latitude and geo_obj.center_longitude:
                    ok, res, _ = dispatch_device_command(
                        device, "geofence_on",
                        radius=radius_val,
                        lat=geo_obj.center_latitude,
                        lon=geo_obj.center_longitude
                    )
                else:
                    ok, res, _ = dispatch_device_command(device, "geofence_on", radius=radius_val)
            else:
                ok, res, _ = dispatch_device_command(device, "geofence_off")
            sms_logs.append({"setting": "geofence", "active": geofence_active, "success": ok, "detail": res})

        # د) في حال جهاز iStartek وتم تفعيل أي من التنبيهات الثلاثة، نؤكد تفعيل الاتصال الهاتفي بأرقام الطوارئ
        if device.model == "Istartek_VT200_L" and any([speed_active, vibration_active, geofence_active]):
            time.sleep(1.2)
            ok_call, res_call, _ = dispatch_device_command(device, "enable_call_alerts")
            sms_logs.append({"setting": "istartek_call_alerts", "success": ok_call, "detail": res_call})

        return Response({
            "success": True,
            "message": "تم حفظ الإعدادات وإرسال أوامر التفعيل للجهاز بنجاح.",
            "device_id": device.id,
            "device_model": device.model,
            "sms_execution_logs": sms_logs
        }, status=status.HTTP_200_OK)

class SetSosNumbersView(APIView):
    """
    تعيين أرقام الطوارئ عبر SMS وحفظها محلياً، مع تفعيل الاتصال التلقائي لـ iStartek
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        device = get_object_or_404(GPSDevice, pk=pk, user=request.user)
        
        num1 = request.data.get("sos1")
        num2 = request.data.get("sos2")
        num3 = request.data.get("sos3")
        
        # تنظيف وتنسيق الأرقام المتوفرة فقط
        raw_numbers = [format_syrian_phone(n) for n in [num1, num2, num3] if n]
        if not raw_numbers:
            return Response(
                {"error": "يجب إدخال رقم طوارئ واحد على الأقل"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        executed_commands = []
        success_sent_count = 0

        # 1. في حال جهاز iStartek: إرسال الأرقام دفعة واحدة + تفعيل الاتصال الهاتفي عند الإنذار
        if device.model == "Istartek_VT200_L":
            phones_str = ",".join(raw_numbers[:3])
            ok, res, _ = dispatch_device_command(device, "sos_set", phones=phones_str)
            executed_commands.append({"command": "sos_set", "success": ok, "detail": res})
            if ok:
                success_sent_count += 1
                time.sleep(1.5)
                # تفعيل الاتصال الهاتفي بأرقام SOS عند حدوث أي تنبيه
                ok_call, res_call, _ = dispatch_device_command(device, "enable_call_alerts")
                executed_commands.append({"command": "enable_call_alerts", "success": ok_call, "detail": res_call})
                time.sleep(1.5)
                # تفعيل الرسائل النصية أيضاً
                ok_sms, res_sms, _ = dispatch_device_command(device, "enable_sms_alerts")
                executed_commands.append({"command": "enable_sms_alerts", "success": ok_sms, "detail": res_sms})

        # 2. في حال جهاز Coban: إرسال كل رقم بأمر مستقل (تفعيل تنبيهات SMS الافتراضية)
        elif device.model == "Copon_303F":
            for phone in raw_numbers:
                ok, res, _ = dispatch_device_command(device, "sos_set", phone=phone)
                executed_commands.append({"command": f"sos_set ({phone})", "success": ok, "detail": res})
                if ok:
                    success_sent_count += 1
                time.sleep(1.5)
        else:
            return Response(
                {"error": f"نوع الجهاز ({device.model}) غير مدعوم."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        if success_sent_count == 0:
            return Response(
                {"error": "فشل إرسال رسائل الأوامر بالكامل عبر بوابة Textbee", "details": executed_commands}, 
                status=status.HTTP_502_BAD_GATEWAY
            )
            
        # حفظ وتحديث البيانات محلياً في جدول EmergencyNumbers
        emergency_obj, _ = EmergencyNumbers.objects.update_or_create(
            device=device,
            defaults={
                "sos_number_1": num1 if num1 else None,
                "sos_number_2": num2 if num2 else None,
                "sos_number_3": num3 if num3 else None,
            }
        )
        
        return Response({
            "success": True,
            "message": "تم ضبط أرقام الطوارئ وتفعيل التنبيهات المناسبة للجهاز بنجاح.",
            "device_name": device.name,
            "device_model": device.model,
            "executed_commands": executed_commands,
            "saved_locally": {
                "sos_number_1": emergency_obj.sos_number_1,
                "sos_number_2": emergency_obj.sos_number_2,
                "sos_number_3": emergency_obj.sos_number_3,
            }
        }, status=status.HTTP_200_OK)


# 2. كلاس جلب أرقام الطوارئ المخزنة في الموديل الجديد
class GetSosNumbersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            # جلب السجل المرتبط بالجهاز عبر الـ related_name المكتوب في الموديل
            emergency_info = device.emergency_numbers
            sos_numbers = {
                "sos1": emergency_info.sos_number_1,
                "sos2": emergency_info.sos_number_2,
                "sos3": emergency_info.sos_number_3,
                "updated_at": emergency_info.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            }
        except EmergencyNumbers.DoesNotExist:
            # في حال لم يتم تعيين أرقام طوارئ لهذا الجهاز من قبل
            sos_numbers = {
                "sos1": None,
                "sos2": None,
                "sos3": None,
                "updated_at": None
            }
            
        return Response({
            "success": True,
            "device_name": device.name,
            "imei": device.imei,
            "sos_numbers": sos_numbers
        }, status=status.HTTP_200_OK)

###############################


class EngineControlSMSAPI(APIView):
    """
    API آمن لإيقاف وتشغيل المحرك عبر SMS مع التحقق من رمز PIN وفحص السرعة الحالية من Traccar
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device_id = request.data.get('device_id')
        action = str(request.data.get('action', '')).strip().lower()  # 'stop' أو 'resume'
        pin_code = str(request.data.get('pin_code', '')).strip()

        if not device_id or action not in ['stop', 'resume']:
            return Response(
                {"error": "يرجى إرسال 'device_id' وتحديد 'action' بقيمة 'stop' أو 'resume'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. جلب الجهاز والتأكد من ملكيته للمستخدم
        device = get_object_or_404(GPSDevice, id=device_id, user=request.user)

        # 2. التحقق من رمز الأمان (PIN Code) إذا كان مضبوطاً على الجهاز
        if device.pin_code:
            if not pin_code:
                return Response(
                    {"error": "يرجى إدخال رمز الأمان (pin_code) لتنفيذ هذه العملية."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # يدعم المقارنة سواء كان الرمز مشفراً بـ Django أو نصاً عادياً
            is_valid_pin = check_password(pin_code, device.pin_code) or (pin_code == device.pin_code)
            if not is_valid_pin:
                return Response(
                    {"error": "رمز الأمان (PIN) غير صحيح."},
                    status=status.HTTP_403_FORBIDDEN
                )

        # 3. فحص الأمان: منع إيقاف المحرك إذا كانت سرعة السيارة أكبر من 20 كم/ساعة
        if action == 'stop':
            base_url = settings.TRACCAR_SERVER_URL.rstrip('/')
            traccar_url = f"{base_url}/api/positions"
            auth_credentials = (settings.TRACCAR_USER, settings.TRACCAR_PASSWORD)

            try:
                pos_res = requests.get(
                    traccar_url,
                    auth=auth_credentials,
                    params={'deviceId': device.traccar_id},
                    timeout=5.0
                )
                if pos_res.status_code == 200 and pos_res.json():
                    latest_pos = pos_res.json()[-1]
                    # Traccar يعيد السرعة بالعقدة (Knots)، نضرب في 1.852 للتحويل إلى كم/ساعة
                    speed_knots = latest_pos.get('speed', 0.0)
                    speed_kmh = round(speed_knots * 1.852, 1)

                    if speed_kmh > 20.0:
                        return Response({
                            "error": f"لا يمكن إيقاف المحرك حفاظاً على السلامة؛ سرعة المركبة الحالية ({speed_kmh} كم/س) تتجاوز الحد المسموح (20 كم/س).",
                            "current_speed_kmh": speed_kmh
                        }, status=status.HTTP_400_BAD_REQUEST)
            except requests.exceptions.RequestException:
                # في حال تعذر الاتصال بـ Traccar مؤقتاً، نرفض الإيقاف احتياطاً أو نكمل حسب سياستك
                return Response(
                    {"error": "تعذر التحقق من سرعة المركبة الحالية من السيرفر، يرجى المحاولة بعد قليل."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )

        # 4. التحقق من رقم الشريحة وتنسيقه
        if not device.phone_number:
            return Response(
                {"error": "لا يوجد رقم شريحة مسجل لهذا الجهاز."},
                status=status.HTTP_400_BAD_REQUEST
            )

        phone = device.phone_number.strip()
        if phone.startswith('09'):
            phone = '+963' + phone[1:]
        elif not phone.startswith('+'):
            phone = '+' + phone

        # 5. اختيار الكود المناسب لموديل الجهاز
        commands_map = {
            'Copon_303F': {
                'stop': 'stop123456',
                'resume': 'resume123456',
            },
            'Istartek_VT200_L': {
                'stop': '0000,900,1,1',
                'resume': '0000,900,1,0',
            }
        }

        device_commands = commands_map.get(device.model)
        if not device_commands:
            return Response(
                {"error": f"نوع الجهاز ({device.model}) غير مدعوم."},
                status=status.HTTP_400_BAD_REQUEST
            )

        sms_command = device_commands[action]

        # 6. إرسال الرسالة النصية عبر Textbee
        success, result = send_textbee_sms([phone], sms_command)

        if success:
            action_ar = "إيقاف المحرك" if action == "stop" else "تشغيل المحرك"
            return Response({
                "status": "success",
                "message": f"تم إرسال أمر {action_ar} بنجاح إلى ({device.name})",
                "sent_to": phone,
                "command": sms_command,
                "gateway_response": result
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "فشل إرسال رسالة التحكم عبر بوابة SMS",
                "details": result
            }, status=status.HTTP_502_BAD_GATEWAY)


class EngineControlSMSAPI_aa(APIView):
    """
    API لإيقاف وتشغيل المحرك عبر SMS بناءً على نوع الجهاز (Copon_303F أو Istartek_VT200_L)
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device_id = request.data.get('device_id')
        action = str(request.data.get('action', '')).strip().lower()  # 'stop' أو 'resume'

        if not device_id or action not in ['stop', 'resume']:
            return Response(
                {"error": "يرجى إرسال 'device_id' وتحديد 'action' بقيمة 'stop' للإيقاف أو 'resume' للتشغيل."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. جلب الجهاز والتأكد من ملكيته للمستخدم الحالي
        device = get_object_or_404(GPSDevice, id=device_id, user=request.user)

        # 2. التحقق من وجود رقم شريحة مسجل للجهاز
        if not device.phone_number:
            return Response(
                {"error": "لا يوجد رقم شريحة (phone_number) مسجل لهذا الجهاز."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. تنسيق رقم الهاتف بصيغة دولية (+963...) لتقبله بوابة Textbee
        phone = device.phone_number.strip()
        if phone.startswith('09'):
            phone = '+963' + phone[1:]
        elif not phone.startswith('+'):
            phone = '+' + phone

        # 4. تحديد كود الـ SMS بناءً على موديل الجهاز والأمر المطلوب
        commands_map = {
            'Copon_303F': {
                'stop': 'stop123456',
                'resume': 'resume123456',
            },
            'Istartek_VT200_L': {
                'stop': '0000,900,1,1',
                'resume': '0000,900,1,0',
            }
        }

        device_commands = commands_map.get(device.model)
        if not device_commands:
            return Response(
                {"error": f"نوع الجهاز ({device.model}) غير مدعوم لأوامر التحكم بالمحرك."},
                status=status.HTTP_400_BAD_REQUEST
            )

        sms_command = device_commands[action]

        # 5. إرسال الرسالة عبر بوابة Textbee
        success, result = send_textbee_sms([phone], sms_command)

        if success:
            action_ar = "إيقاف المحرك" if action == "stop" else "تشغيل المحرك"
            return Response({
                "status": "success",
                "message": f"تم إرسال أمر {action_ar} بنجاح إلى الجهاز ({device.name})",
                "device_model": device.model,
                "sent_to": phone,
                "command": sms_command,
                "gateway_response": result
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "فشل إرسال رسالة التحكم عبر بوابة SMS",
                "details": result
            }, status=status.HTTP_502_BAD_GATEWAY)

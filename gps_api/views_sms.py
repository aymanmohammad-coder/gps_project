# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from gps_api.models import GPSDevice
from django.http import JsonResponse
import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

# استيراد الموديل والدالة العامة
from gps_api.models import GPSDevice ,EmergencyNumbers # تأكد من مسار الموديل الصحيح لديك
from gps_api.utils import send_textbee_sms

# Serializer للتأكد من صحة المدخلات في الـ JSON Body
class MicModeSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=['monitor', 'tracker'], 
        error_messages={'invalid_choice': "الحالة المرسلة غير مدعومة. يرجى إرسال 'monitor' أو 'tracker'"}
    )



@api_view(['POST']) # تأكد من تحديد نوع الطلب إذا كنت تستخدم DRF
def stop_engine_via_sms_gateway_ok_ayman(request):
    traccar_id = request.data.get('traccar_id')
    
    if not traccar_id:
        return JsonResponse({"error": "traccar_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
    # رقم الشريحة الفعلي المتواجد داخل السيارة
    device_phone = "+963946620316"  
    
    # رابط خدمة Textbee ومفتاح الـ API المخزن في settings
    url = "https://api.textbee.dev/api/v1/gateway/send-sms"
    
    # يفضل تخزين مفتاح الـ API في ملف settings.py الخاص بديانغو لحمايته
    api_key = getattr(settings, 'TEXTBEE_API_KEY', None)
    
    if not api_key:
        return JsonResponse({
            "error": "Textbee API key is not configured in settings."
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    headers = {
        'Content-Type': 'application/json',
        'x-api-key': api_key,
    }
    
    # تجهيز البيانات بحسب متطلبات Textbee (recipients كـ List و message كـ String)
    sms_payload = {
        "recipients": [device_phone],
      #"message": "resume123456"  # كود الفصل الخاص بجهاز التتبع
       "message": "stop123456"  # كود الفصل الخاص بجهاز التتبع
    }
    
    try:
        response = requests.post(
            url,
            json=sms_payload,
            headers=headers,
            timeout=15
        )
        
        # قبول الاستجابة الناجحة من Textbee
        if response.status_code in[200,201,202]:
            return JsonResponse({
                "success": True,
                "message": "Stop engine SMS command queued successfully via Textbee",
                "gateway_response": response.json()
            }, status=status.HTTP_200_OK)
        else:
            return JsonResponse({
                "error": "Textbee gateway rejected the request",
                "status_code": response.status_code,
                "gateway_response": response.text
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except requests.exceptions.RequestException as e:
        return JsonResponse({
            "error": f"External Textbee gateway connection failed: {str(e)}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



# 1. كلاس الـ API الخاص بتفعيل وإلغاء وضع المايك (التنصت) عبر الـ SMS
class ToggleMicView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            # جلب الجهاز بناءً على المعرف والملكية لحماية البيانات دون الحاجة لـ traccar_id من العميل
            device = GPSDevice.objects.get(pk=pk, user=request.user)
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

        # جلب رقم الهاتف من موديل الجهاز ديناميكياً، أو استخدام الرقم الافتراضي كاحتياط
        device_phone = getattr(device, 'phone', "+963946620316") or "+963946620316"

        # استدعاء الدالة العامة من utils
        success, result = send_textbee_sms([device_phone], commands[mode])

        if success:
            return Response({
                "success": True,
                "status": "sent",
                "message": f"تم إرسال أمر {mode} بنجاح عبر الرسائل النصية للسيارة",
                "device_name": device.name,
                "imei": device.imei,
                "gateway_response": result
            }, status=status.HTTP_200_OK)

        return Response({
            "success": False,
            "status": "failed",
            "error": result
        }, status=status.HTTP_502_BAD_GATEWAY)


# 2. كلاس الـ API الخاص بإيقاف محرك السيارة عبر الـ SMS
class StopEngineView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            # جلب الجهاز بناءً على الـ pk الممرر بالرابط وحساب المستخدم المسجل
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"},
                status=status.HTTP_404_NOT_FOUND
            )

        # جلب رقم الهاتف المسجل على الجهاز أو الرقم الافتراضي
        device_phone = getattr(device, 'phone', "+963946620316") or "+963946620316"
        
        # كود فصل المحرك الثابت
        #command_message = "resume123456"
        command_message = "stop123456"

        # استدعاء الدالة العامة من utils
        success, result = send_textbee_sms([device_phone], command_message)

        if success:
            return Response({
                "success": True,
                "status": "sent",
                "message": "تم إرسال أمر إيقاف المحرك بنجاح عبر الرسائل النصية للسيارة",
                "device_name": device.name,
                "imei": device.imei,
                "gateway_response": result
            }, status=status.HTTP_200_OK)

        return Response({
            "success": False,
            "status": "failed",
            "error": result
        }, status=status.HTTP_502_BAD_GATEWAY)



# 1. كلاس تعيين وحفظ أرقام الطوارئ عبر الـ SMS وقاعدة البيانات
class SetSosNumbersView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # استقبال الأرقام المرسلة من واجهة المستخدم
        num1 = request.data.get("sos1")
        num2 = request.data.get("sos2")
        num3 = request.data.get("sos3")
        
        # إنشاء مصفوفة بالأرقام المتوفرة فقط (تجاهل الحقول الفارغة)
        raw_numbers = [n for n in [num1, num2, num3] if n]
        if not raw_numbers:
            return Response(
                {"error": "يجب إدخال رقم طوارئ واحد على الأقل"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # جلب رقم هاتف شريحة السيارة ديناميكياً لإرسال الـ SMS إليه
        device_phone = getattr(device, 'phone', "+963946620316") or "+963946620316"
        
        # بناء أوامر الـ SMS التسلسلية المتوافقة مع أجهزة التتبع (مثل: admin123456 0933...)
        commands = [f"admin123456 {num}" for num in raw_numbers]
        success_sent_count = 0
        
        for cmd in commands:
            success, _ = send_textbee_sms([device_phone], cmd)
            if success:
                success_sent_count += 1
                
        if success_sent_count == 0:
            return Response(
                {"error": "فشل إرسال رسائل الأوامر بالكامل عبر بوابة Textbee"}, 
                status=status.HTTP_502_BAD_GATEWAY
            )
            
        # 💾 حفظ وتحديث البيانات محلياً في جدول EmergencyNumbers عند نجاح الإرسال
        # استخدام update_or_create للتعامل مع الـ OneToOneField بسلاسة
        emergency_obj, created = EmergencyNumbers.objects.update_or_create(
            device=device,
            defaults={
                "sos_number_1": num1 if num1 else None,
                "sos_number_2": num2 if num2 else None,
                "sos_number_3": num3 if num3 else None,
            }
        )
        
        return Response({
            "success": True,
            "message": "تمت معالجة الأوامر وتحديث الأرقام محلياً بنجاح.",
            "device_name": device.name,
            "commands_sent_successfully": f"{success_sent_count} من أصل {len(commands)}",
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

class SetSosNumbersView1(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            # جلب الجهاز بناءً على المعرف وملكية المستخدم لحماية البيانات
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # نأخذ الأرقام كما هي مدخلة من المستخدم
        num1 = request.data.get("sos1")
        num2 = request.data.get("sos2")
        num3 = request.data.get("sos3")
        
        raw_numbers = [n for n in [num1, num2, num3] if n]
        if not raw_numbers:
            return Response(
                {"error": "يجب إدخال رقم طوارئ واحد على الأقل"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # جلب رقم هاتف شريحة جهاز التتبع ديناميكياً
        device_phone = getattr(device, 'phone', "+963946620316") or "+963946620316"
        
        # بناء الأوامر النصية بحسب الأرقام المدخلة
        commands = [f"admin123456 {num}" for num in raw_numbers]
        
        success_sent_count = 0
        failed_commands = []

        # إرسال كل أمر عبر الـ SMS كرسالة مستقلة للجهاز
        for i, cmd in enumerate(commands):
            # نمرر رقم شريحة الجهاز كـ list ونص الأمر كـ message
            success, result = send_textbee_sms([device_phone], cmd)
            if success:
                success_sent_count += 1
            else:
                # الاحتفاظ بالرقم الذي فشل إرسال أمره لمراجعة العميل
                failed_commands.append(raw_numbers[i])
                
        if success_sent_count == 0:
            return Response(
                {"error": "فشل إرسال أوامر الطوارئ بالكامل عبر بوابة الرسائل النصية"}, 
                status=status.HTTP_502_BAD_GATEWAY
            )
            
        # إرجاع استجابة واضحة تبيّن كم عدد الرسائل التي تم جدولتها بنجاح
        return Response({
            "success": True,
            "message": "تمت معالجة إرسال أرقام الطوارئ بنجاح عبر الـ SMS.",
            "device_name": device.name,
            "imei": device.imei,
            "commands_sent_successfully": f"{success_sent_count} من أصل {len(commands)}",
            "failed_numbers": failed_commands
        }, status=status.HTTP_200_OK)


class GetSosNumbersView1(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            # جلب الجهاز بناءً على المعرف والملكية لحماية الخصوصية
            device = GPSDevice.objects.get(pk=pk, user=request.user)
        except GPSDevice.DoesNotExist:
            return Response(
                {"error": "الجهاز غير موجود أو لا تملك صلاحية الوصول إليه"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # جلب الأرقام المخزنة في حقول الموديل (تأكد من مطابقة أسماء الحقول لديك)
        sos_numbers = {
            "sos1": getattr(device, 'sos1', None),
            "sos2": getattr(device, 'sos2', None),
            "sos3": getattr(device, 'sos3', None),
        }
        
        return Response({
            "success": True,
            "device_name": device.name,
            "imei": device.imei,
            "sos_numbers": sos_numbers
        }, status=status.HTTP_200_OK)

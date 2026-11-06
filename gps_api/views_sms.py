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
from gps_api.models import GPSDevice  # تأكد من مسار الموديل الصحيح لديك
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

# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import GPSDevice
from .utils import send_sms_via_android  # استيراد الدالة المحدثة أعلاه

# Serializer للتأكد من صحة المدخلات في الـ JSON Body
class MicModeSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=['monitor', 'tracker'], 
        error_messages={'invalid_choice': "الحالة المرسلة غير مدعومة. يرجى إرسال 'monitor' أو 'tracker'"}
    )

class ToggleMicView(APIView):
    permission_classes = [IsAuthenticated]  # حماية الـ API للمستخدمين المسجلين فقط

    def post(self, request, pk):
        # 1. جلب الجهاز والتأكد من ملكيته للمستخدم الحالي حمايةً للخصوصية
        device = get_object_or_404(GPSDevice, pk=pk, user=request.user)
        
        # التحقق من أن الموديل يحتوي على رقم الشريحة الخاص بالمركبة
        if not hasattr(device, 'phone_number') or not device.phone_number:
            return Response(
                {"error": "لم يتم تسجيل رقم شريحة الـ SIM الخاصة بجهاز التتبع في قاعدة البيانات"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 2. التحقق من البيانات القادمة في الطلب {"mode": "monitor"}
        serializer = MicModeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        mode = serializer.validated_data['mode']
        
        # 3. صياغة الأمر الصريح والنهائي لأجهزة Coban عبر الـ SMS
        command = "monitor123456" if mode == "monitor" else "tracker123456"
        
        # 4. إرسال الطلب إلى هاتف الأندرويد ليقوم بدوره بإرسال الـ SMS
        success = send_sms_via_android(device.phone_number, command)
        
        if success:
            return Response({
                "message": f"تم إرسال أمر التحويل إلى وضع {mode} بنجاح عبر بوابة الأندرويد كـ SMS.",
                "device_name": device.name,
                "target_phone": device.phone_number
            }, status=status.HTTP_200_OK)
            
        return Response(
            {"error": "فشل إرسال الرسالة النصية. يرجى التأكد من اتصال هاتف الأندرويد بالإنترنت وصلاحية الحساب"}, 
            status=status.HTTP_502_BAD_GATEWAY
        )
